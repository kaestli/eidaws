# -*- coding: utf-8 -*-
import aiohttp
import aioredis
import asyncio
import datetime
import functools
import logging

from aiohttp import web

from eidaws.federator.utils.request import RoutingRequestHandler
from eidaws.federator.settings import FED_BASE_ID, FED_DEFAULT_NETLOC_PROXY
from eidaws.federator.utils import misc
from eidaws.federator.utils.httperror import FDSNHTTPError
from eidaws.federator.version import __version__
from eidaws.utils.error import ErrorWithTraceback
from eidaws.utils.settings import (
    FDSNWS_DEFAULT_NO_CONTENT_ERROR_CODE,
    FDSNWS_NO_CONTENT_CODES,
)
from eidaws.utils.sncl import StreamEpoch


def with_redis_exception_handling(propagate_exceptions=False):
    """
    Returns a method decorator providing Redis exception handling facilities.
    """

    def propagate_exception(err):
        if propagate_exceptions:
            raise err

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            try:
                return await func(self, *args, **kwargs)
            except asyncio.TimeoutError as err:
                self.logger.warning(
                    f"Timout while connecting Redis: {type(err)}"
                )
                propagate_exception(err)
            except (OSError, aioredis.RedisError) as err:
                self.logger.warning(f"Error while connecting Redis: {err}")
                propagate_exception(err)

        return wrapper

    return decorator


def cached(func):
    """
    Method decorator providing caching facilities.
    """

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        cache_key = self.make_cache_key(
            self.query_params, self.stream_epochs, key_prefix=type(self)
        )

        cached, found = await self.get_cache(cache_key)

        self._await_on_close.insert(
            0, functools.partial(self.set_cache, cache_key)
        )

        if found:
            return web.Response(
                content_type=self.content_type,
                charset=self.charset,
                body=cached,
            )

        return await func(self, *args, **kwargs)

    return wrapper


class RequestProcessorError(ErrorWithTraceback):
    """Base RequestProcessor error ({})."""


class BaseRequestProcessor:
    """
    Abstract base class for request processors.
    """

    LOGGER = FED_BASE_ID + ".process"

    ACCESS = "any"

    def __init__(self, request, url_routing, **kwargs):
        """
        :param float retry_budget_client: Per client retry-budget in percent.
            The value defines the cut-off error ratio above requests to
            datacenters (DC) are dropped.
        """

        self.request = request

        self._url_routing = url_routing

        self._proxy_netloc = kwargs.get(
            "proxy_netloc", FED_DEFAULT_NETLOC_PROXY
        )

        self._default_endtime = datetime.datetime.utcnow()
        self._post = False

        self._routing_table = {}
        self._tasks = []
        self._await_on_close = [
            self._gc_response_code_stats,
            self._teardown_tasks,
        ]

        self._logger = logging.getLogger(self.LOGGER)
        self.logger = misc.make_context_logger(self._logger, self.request)

    @property
    def query_params(self):
        return self.request[FED_BASE_ID + ".query_params"]

    @property
    def stream_epochs(self):
        return self.request[FED_BASE_ID + ".stream_epochs"]

    @property
    def request_submitted(self):
        return self.request[FED_BASE_ID + ".request_starttime"]

    @property
    def nodata(self):
        return int(
            self.query_params.get(
                "nodata", FDSNWS_DEFAULT_NO_CONTENT_ERROR_CODE
            )
        )

    @property
    def post(self):
        return self._post

    @post.setter
    def post(self, val):
        self._post = bool(val)

    @property
    def content_type(self):
        raise NotImplementedError

    @property
    def charset(self):
        return None

    @property
    def pool_size(self):
        return None

    @property
    def stats_retry_budget_client(self):
        return self.request.app["response_code_statistics"]

    @property
    def client_retry_budget_threshold(self):
        raise NotImplementedError

    def _handle_error(self, result):
        self.logger.warning(result)

    _handle_413 = _handle_error

    async def _route(self, timeout=aiohttp.ClientTimeout(total=2 * 60)):
        def emerge_routing_table(text, post, default_endtime):
            """
            Parse the routing service's output stream and create a routing
            table.
            """

            urlline = None
            stream_epochs = []

            routing_table = {}
            for line in text.split("\n"):
                if not urlline:
                    urlline = line.strip()
                elif not line.strip():
                    # set up the routing table
                    if stream_epochs:
                        routing_table[urlline] = stream_epochs

                    urlline = None
                    stream_epochs = []

                else:
                    # XXX(damb): Do not substitute an empty endtime when
                    # performing HTTP GET requests in order to guarantee
                    # more cache hits (if eida-federator is coupled with
                    # HTTP caching proxy).
                    stream_epochs.append(
                        StreamEpoch.from_snclline(
                            line,
                            default_endtime=(
                                default_endtime if post else None
                            ),
                        )
                    )

            return routing_table

        req_handler = RoutingRequestHandler(
            self._url_routing,
            self.stream_epochs,
            self.query_params,
            proxy_netloc=self._proxy_netloc,
            access=self.ACCESS,
        )

        async with aiohttp.ClientSession(
            connector=self.request.app["routing_http_conn_pool"],
            timeout=timeout,
            connector_owner=False,
        ) as session:
            req = (
                req_handler.post(session)
                if self._post
                else req_handler.get(session)
            )

            async with req() as resp:
                self.logger.debug(
                    f"Response: {resp.reason}: resp.status={resp.status}, "
                    f"resp.request_info={resp.request_info}, "
                    f"resp.url={resp.url}, resp.headers={resp.headers}"
                )

                if resp.status in FDSNWS_NO_CONTENT_CODES:
                    raise FDSNHTTPError.create(
                        self.nodata,
                        self.request,
                        request_submitted=self.request_submitted,
                        service_version=__version__,
                    )

                try:
                    resp.raise_for_status()
                except aiohttp.ClientResponseError as err:
                    self.logger.exception(err)
                    raise FDSNHTTPError.create(
                        500,
                        self.request,
                        request_submitted=self.request_submitted,
                        service_version=__version__,
                    )

                if resp.status != 200:
                    self.logger.error(f"Error while routing: {resp}")
                    raise FDSNHTTPError.create(
                        500,
                        self.request,
                        request_submitted=self.request_submitted,
                        service_version=__version__,
                    )

                return emerge_routing_table(
                    await resp.text(),
                    post=self._post,
                    default_endtime=self._default_endtime,
                )

    @cached
    async def federate(self, timeout=aiohttp.ClientTimeout(total=60)):
        try:
            self._routing_table = await self._route()
        except asyncio.TimeoutError as err:
            self.logger.warning(f"TimeoutError while routing: {type(err)}")
            raise FDSNHTTPError.create(
                500,
                self.request,
                request_submitted=self.request_submitted,
                service_version=__version__,
            )

        if not self._routing_table:
            raise FDSNHTTPError.create(
                self.nodata,
                self.request,
                request_submitted=self.request_submitted,
                service_version=__version__,
            )

        self.logger.debug(
            f"Number of routes received: {len(self._routing_table)}"
        )

        response = await self._make_response(
            self._routing_table, timeout=timeout
        )

        await asyncio.shield(self.finalize())

        return response

    async def _make_response(
        self, routing_table, timeout=aiohttp.ClientTimeout(total=60)
    ):
        """
        Template method to be implemented by concrete processor
        implementations.
        """

        raise NotImplementedError

    @with_redis_exception_handling(propagate_exceptions=True)
    async def get_cretry_budget_error_ratio(self, url):
        """
        Return the error ratio of a response code time series referenced by
        ``url``.

        :param str url: URL indicating the response code time series to be
            garbage collected

        :returns: Error ratio in percent
        :rtype: float
        """
        return 100 * await self.stats_retry_budget_client.get_error_ratio(url)

    @with_redis_exception_handling(propagate_exceptions=True)
    async def update_cretry_budget(self, url, code):
        """
        Add ``code`` to the response code time series referenced by
        ``url``.

        :param str url: URL indicating the response code time series to be
            garbage collected
        :param int code: HTTP status code to be appended
        """
        await self.stats_retry_budget_client.add(url, code)

    @with_redis_exception_handling()
    async def gc_cretry_budget(self, url):
        """
        Garbage collect the response code time series referenced by ``url``.

        :param str url: URL indicating the response code time series to be
            garbage collected
        """
        await self.stats_retry_budget_client.gc(url)

    async def finalize(self, **kwargs):
        """
        Finalize the response.
        """

        for coro in self._await_on_close:
            await coro(**kwargs)

    async def _teardown_tasks(self, **kwargs):

        return_exceptions = kwargs.get("return_exceptions", True)

        self.logger.debug("Teardown worker tasks ...")
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=return_exceptions)

    async def _gc_response_code_stats(self):

        self.logger.debug("Garbage collect response code statistics ...")

        for url in self._routing_table.keys():
            await self.gc_cretry_budget(url)
