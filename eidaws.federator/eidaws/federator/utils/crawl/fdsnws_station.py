# -*- coding: utf-8 -*-

import aiohttp
import argparse
import asyncio
import datetime
import logging
import logging.config
import logging.handlers  # needed for handlers defined in logging.conf
import os
import re
import sys
import traceback

from copy import deepcopy
from itertools import product
from urllib.parse import urlparse, urlunparse, urljoin
from random import randint

from cached_property import cached_property
from fasteners import InterProcessLock

from eidaws.federator.version import __version__
from eidaws.federator.utils.crawl.settings import (
    FED_CRAWL_STATION_BASE_ID,
    FED_CRAWL_STATION_DEFAULT_CONFIG_FILES,
    FED_CRAWL_STATION_DEFAULT_URL_FED,
    FED_CRAWL_STATION_DEFAULT_URL_STL,
    FED_CRAWL_STATION_DEFAULT_ORIGINAL_EPOCHS,
    FED_CRAWL_STATION_DEFAULT_NETWORK,
    FED_CRAWL_STATION_DEFAULT_STATION,
    FED_CRAWL_STATION_DEFAULT_LOCATION,
    FED_CRAWL_STATION_DEFAULT_CHANNEL,
    FED_CRAWL_STATION_DEFAULT_FORMAT,
    FED_CRAWL_STATION_DEFAULT_LEVEL,
    FED_CRAWL_STATION_DEFAULT_DOMAIN,
    FED_CRAWL_STATION_DEFAULT_CONFIG_FILES,
    FED_CRAWL_STATION_DEFAULT_PATH_PIDFILE,
    FED_CRAWL_STATION_DEFAULT_PATH_LOGGING_CONF,
    FED_CRAWL_STATION_DEFAULT_NUM_WORKERS,
    FED_CRAWL_STATION_DEFAULT_TIMEOUT,
)
from eidaws.federator.utils.pool import Pool
from eidaws.federator.utils.request import FdsnRequestHandler
from eidaws.federator.utils.worker import with_exception_handling
from eidaws.utils.app import AppError
from eidaws.utils.cli import CustomParser, InterpolatingYAMLConfigFileParser
from eidaws.utils.error import Error, ExitCodes
from eidaws.utils.misc import real_file_path
from eidaws.utils.settings import (
    EIDAWS_ROUTING_PATH_QUERY,
    FDSNWS_STATION_PATH_QUERY,
    FDSNWS_NO_CONTENT_CODES,
)
from eidaws.utils.sncl import StreamEpoch


class AlreadyCrawling(Error):
    """There seems to be a crawler process already in action ({})"""


class RoutingError(Error):
    """Error while requesting routing information ({})"""


class CrawlFDSNWSStationApp:
    """
    Implementation of a crawler application for `fdsnws-station`.
    """

    PROG = "eida-crawl-fdsnws-station"

    _HEADERS = {"User-Agent": "EIDA-Crawler/" + __version__}

    # NOTE(damb): From https://github.com/kvesteri/validators
    _PATTERN_DOMAIN = re.compile(
        r"^(?:[a-zA-Z0-9]"  # First character of the domain
        r"(?:[a-zA-Z0-9-_]{0,61}[A-Za-z0-9])?\.)"  # Sub domain + hostname
        r"+[A-Za-z0-9][A-Za-z0-9-_]{0,61}"  # First 61 characters of the gTLD
        r"[A-Za-z]$"  # Last character of the gTLD
    )

    @cached_property
    def config(self):
        def configure_logging(config_dict):
            try:
                path_logging_conf = real_file_path(
                    config_dict["path_logging_conf"]
                )
            except (KeyError, TypeError):
                path_logging_conf = None

            self.logger = self._setup_logger(
                path_logging_conf, capture_warnings=True
            )

        parser = self._build_parser()
        args = vars(parser.parse_args())

        # validate args (parameter dependency)
        if (
            len(args["format"]) == 1
            and args["format"][0] == "text"
            and len(args["level"]) == 1
            and args["level"][0] == "response"
        ):
            parser.error(
                "Invalid configuration: --format text --level response"
            )

        configure_logging(args)
        return args

    async def run(self):
        """
        Run application.
        """
        exit_code = ExitCodes.EXIT_SUCCESS

        self.logger.info(f"{self.PROG}: Version v{__version__}")
        self.logger.debug(f"Configuration: {dict(self.config)!r}")

        try:
            pid_lock, got_pid_lock = self._get_pid_lock(
                self.config["path_pidfile"]
            )

            net_codes = ",".join(self.config["network"])
            sta_codes = ",".join(self.config["station"])
            loc_codes = ",".join(self.config["location"])
            cha_codes = ",".join(self.config["channel"])

            connector = aiohttp.TCPConnector(
                limit=self.config["worker_pool_size"]
            )
            timeout = aiohttp.ClientTimeout(total=self.config["timeout"])
            stream_epochs_received = False
            async with aiohttp.ClientSession(
                connector=connector, headers=self._HEADERS
            ) as session:
                async with Pool(
                    worker_coro=self._request_worker,
                    max_workers=self.config["worker_pool_size"],
                ) as pool:
                    for level in self.config["level"]:
                        stream_epochs = await self._emerge_stream_epochs(
                            session,
                            net_codes,
                            sta_codes,
                            loc_codes,
                            cha_codes,
                            level,
                        )

                        self.logger.debug(
                            f"Received {len(stream_epochs)} stream epoch(s)"
                        )
                        if stream_epochs:
                            self.logger.info(f"Start crawling (level={level})")
                            await self._crawl(
                                pool,
                                session,
                                stream_epochs,
                                level,
                                timeout=timeout,
                            )

                            stream_epochs_received = True
                        

            if stream_epochs_received:
                self.logger.info("Finished crawling successfully")
            else:
                self.logger.info("Nothing to do")

        except Error as err:
            self.logger.error(err)
            exit_code = ExitCodes.EXIT_ERROR
        except Exception as err:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            self.logger.critical("Local Exception: %s" % err)
            self.logger.critical(
                "Traceback information: "
                + repr(
                    traceback.format_exception(
                        exc_type, exc_value, exc_traceback
                    )
                )
            )
            exit_code = ExitCodes.EXIT_ERROR
        finally:
            try:
                if got_pid_lock:
                    pid_lock.release()
            except NameError:
                pass

        sys.exit(exit_code)

    async def _emerge_stream_epochs(
        self,
        session,
        net_codes,
        sta_codes,
        loc_codes,
        cha_codes,
        level,
        domains=None,
    ):
        """
        Emerge stream epochs using eidaws-stationlite.
        """

        async def _request(url, params):
            def _parse_stream_epochs(text, domains=None):
                stream_epochs = []
                url = None
                skip_url = False
                for line in text.split("\n"):
                    if not url:
                        url = line.strip()
                        if domains is not None:
                            parsed = urlparse(url)
                            if parsed.netloc not in domains:
                                skip_url = True

                    elif not line.strip():
                        url = None
                        skip_url = False

                    else:
                        if skip_url:
                            continue

                        se = StreamEpoch.from_snclline(line)
                        stream_epochs.append(se)

                return stream_epochs

            async with session.get(url, params=params) as resp:
                self.logger.debug(
                    f"Response: {resp.reason}: resp.status={resp.status}, "
                    f"resp.request_info={resp.request_info}, "
                    f"resp.url={resp.url}, resp.headers={resp.headers}"
                )

                if resp.status != 200:
                    raise RoutingError(f"{resp}")

                return _parse_stream_epochs(
                    await resp.text(), domains=self.config["domain"]
                )

        url_routing = urljoin(
            self.config["routing_url"], EIDAWS_ROUTING_PATH_QUERY
        )
        params = {
            "network": net_codes,
            "station": sta_codes,
            "location": loc_codes,
            "channel": cha_codes,
            "level": level,
            "service": "station",
            # "merge": "true",
        }
        stream_epochs = await _request(url_routing, params)
        if self.config["original_epochs"]:
            params["merge"] = "false"
            stream_epochs.extend(await _request(url_routing, params))

        # remove duplicates - maintain order
        return list(dict.fromkeys(stream_epochs))

    @with_exception_handling(ignore_runtime_exception=False)
    async def _request_worker(
        self, session, url, stream_epoch, query_params, **req_kwargs
    ):
        req_handler = FdsnRequestHandler(
            url,
            stream_epochs=[stream_epoch],
            query_params=query_params,
            headers=self._HEADERS,
        )

        resp_status = None
        req = req_handler.get(session)
        try:
            async with req(**req_kwargs) as resp:
                resp.raise_for_status()

                resp_status = resp.status
                self.logger.debug(
                    f"Response: {resp.reason}: resp.status={resp.status}, "
                    f"resp.request_info={resp.request_info}, "
                    f"resp.url={resp.url}, resp.headers={resp.headers}"
                )
                # TODO(damb): collect stats etc.
        except aiohttp.ClientResponseError as err:
            resp_status = err.status
            msg = (
                f"Error while executing request: {err.message}: "
                f"error={type(err)}, resp.status={resp_status}, "
                f"resp.request_info={err.request_info}, "
                f"resp.headers={err.headers}"
            )

            if resp_status in FDSNWS_NO_CONTENT_CODES:
                self.logger.info(msg)
            else:
                self.logger.warning(msg)

        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            resp_status = 503
            msg = (
                f"Error while executing request: error={type(err)}, "
                f"req_handler={req_handler!r}"
            )
            if isinstance(err, aiohttp.ClientOSError):
                msg += f", errno={err.errno}"

            self.logger.warning(msg)

    async def _crawl(self, pool, session, stream_epochs, level, **req_kwargs):
        """
        Crawl ``stream_epochs`` for ``level`` and dispatch ``stream_epochs`` to
        ``pool``.
        """

        url_federator = urljoin(
            self.config["federator_url"], FDSNWS_STATION_PATH_QUERY
        )
        for f in self.config["format"]:
            if f == "text" and level == "response":
                continue

            query_params = {"format": f, "level": level}
            for stream_epoch in stream_epochs:
                self.logger.debug(
                    f"Creating task: stream_epoch={stream_epoch!r}, "
                    f"query_params={query_params!r}"
                )
                await pool.submit(
                    session,
                    url_federator,
                    stream_epoch,
                    query_params,
                    **req_kwargs,
                )

    def _build_parser(self, parents=[]):
        """
        Configure a parser.

        :param list parents: list of parent parsers
        :returns: parser
        :rtype: :py:class:`argparse.ArgumentParser`
        """

        def _abs_path(path):
            if not os.path.isabs(path):
                raise argparse.ArgumentError(
                    f"Not an absolute file path: {path!r}"
                )
            return path

        def _url(url):
            parsed = urlparse(url)
            if not (all([parsed.scheme, parsed.netloc])):
                raise argparse.ArgumentError(f"Invalid URL: {url!r}")

            return urlunparse(parsed)

        # NOTE(damb): Refer to
        # http://docs.fdsn.org/projects/source-identifiers/en/v1.0/definition.html
        def _net_code(code):
            if code and re.match("[A-Z0-9*?]{1,8}$", code):
                return code

            raise argparse.ArgumentError(f"Invalid network code: {code!r}")

        def _sta_code(code):
            if code and re.match("[A-Z0-9*?-]{1,8}$", code):
                return code

            raise argparse.ArgumentError(f"Invalid station code: {code!r}")

        def _loc_code(code):
            if code and re.match("[A-Z0-9*?-]{1,8}$", code):
                return code

            raise argparse.ArgumentError(f"Invalid location code: {code!r}")

        def _cha_code(code):
            if code and re.match("[A-Z0-9*?]{1,3}$", code):
                return code

            raise argparse.ArgumentError(f"Invalid channel code: {code!r}")

        def _positive_int(i):
            try:
                i = int(i)
                if i <= 0:
                    raise ValueError
            except Exception as err:
                raise argparse.ArgumentError(f"Invalid integer value.")

            return i

        def _domain_or_none(domain):
            def to_unicode(obj, charset="utf-8", errors="strict"):
                if obj is None:
                    return None
                if not isinstance(obj, bytes):
                    return str(obj)
                return obj.decode(charset, errors)

            if domain is None:
                return None

            try:
                if self._PATTERN_DOMAIN.match(
                    to_unicode(domain).encode("idna").decode("ascii")
                ):
                    return domain
            except (UnicodeError, AttributeError):
                raise argparse.ArgumentError(
                    f"Invalid domain name: {domain!r}"
                )

            raise argparse.ArgumentError(f"Invalid domain name: {domain!r}")

        parser = CustomParser(
            prog=self.PROG,
            description=(
                "Crawl fdsnws-station with eidaws-stationlite / "
                "eidaws-federator and keep caches hot."
            ),
            parents=parents,
            default_config_files=FED_CRAWL_STATION_DEFAULT_CONFIG_FILES,
            config_file_parser_class=InterpolatingYAMLConfigFileParser,
            args_for_setting_config_path=["-c", "--config"],
        )
        # optional arguments
        parser.add_argument(
            "-V",
            action="version",
            version="%(prog)s version " + __version__,
        )
        parser.add_argument(
            "-R",
            "--routing-url",
            type=_url,
            metavar="URL",
            dest="routing_url",
            default=FED_CRAWL_STATION_DEFAULT_URL_STL,
            help=("eidaws-stationlite URL (default: %(default)s)."),
        )
        parser.add_argument(
            "-F",
            "--federator-url",
            type=_url,
            metavar="URL",
            dest="federator_url",
            default=FED_CRAWL_STATION_DEFAULT_URL_FED,
            help=("eidaws-federator URL (default: %(default)s)."),
        )
        parser.add_argument(
            "--original-epochs",
            action="store_true",
            dest="original_epochs",
            default=FED_CRAWL_STATION_DEFAULT_ORIGINAL_EPOCHS,
            help=(
                "Also crawl epochs fully split i.e. as defined by "
                "fdsnws-station. By default only merged epochs "
                "are crawled."
            ),
        )
        parser.add_argument(
            "--domain",
            nargs="+",
            metavar="DOMAIN",
            type=_domain_or_none,
            default=FED_CRAWL_STATION_DEFAULT_DOMAIN,
            help=(
                "Whitespace-separated list of domains crawling is restricted "
                "to. By default all domains are crawled."
            ),
        )

        parser.add_argument(
            "--network",
            nargs="+",
            metavar="CODE",
            type=_net_code,
            default=FED_CRAWL_STATION_DEFAULT_NETWORK,
            help=(
                "Whitespace-separated list of network codes crawling "
                "is restricted to. Allows FDSNWS wildcard characters to be "
                "used (default: %(default)s)."
            ),
        )
        parser.add_argument(
            "--station",
            nargs="+",
            metavar="CODE",
            type=_sta_code,
            default=FED_CRAWL_STATION_DEFAULT_STATION,
            help=(
                "Whitespace-separated list of station codes crawling "
                "is restricted to. Allows FDSNWS wildcard characters to be "
                "used (default: %(default)s)."
            ),
        )
        parser.add_argument(
            "--location",
            nargs="+",
            metavar="CODE",
            type=_loc_code,
            default=FED_CRAWL_STATION_DEFAULT_LOCATION,
            help=(
                "Whitespace-separated list of location codes crawling "
                "is restricted to. Allows FDSNWS wildcard characters to be "
                "used (default: %(default)s)."
            ),
        )
        parser.add_argument(
            "--channel",
            nargs="+",
            metavar="CODE",
            type=_cha_code,
            default=FED_CRAWL_STATION_DEFAULT_CHANNEL,
            help=(
                "Whitespace-separated list of channel codes crawling "
                "is restricted to. Allows FDSNWS wildcard characters to be "
                "used (default: %(default)s)."
            ),
        )
        parser.add_argument(
            "--format",
            nargs="+",
            metavar="FORMAT",
            default=FED_CRAWL_STATION_DEFAULT_FORMAT,
            choices=sorted(FED_CRAWL_STATION_DEFAULT_FORMAT),
            help=(
                "Whitespace-separated list of formats to "
                "be crawled (choices: {%(choices)s}). "
                "By default all formats choicable are crawled."
            ),
        )
        parser.add_argument(
            "--level",
            nargs="+",
            metavar="LEVEL",
            default=FED_CRAWL_STATION_DEFAULT_LEVEL,
            choices=sorted(FED_CRAWL_STATION_DEFAULT_LEVEL),
            help=(
                "Whitespace-separated list of levels to "
                "be crawled (choices: {%(choices)s}). "
                "By default all levels choicable are crawled."
            ),
        )
        parser.add_argument(
            "-w",
            "--worker-pool-size",
            type=_positive_int,
            metavar="NUM",
            dest="worker_pool_size",
            default=FED_CRAWL_STATION_DEFAULT_NUM_WORKERS,
            help=(
                "Number of concurrently crawling request workers "
                "(default: %(default)s)."
            ),
        )
        parser.add_argument(
            "--timeout",
            type=_positive_int,
            metavar="SEC",
            default=FED_CRAWL_STATION_DEFAULT_TIMEOUT,
            help="Total request timeout in seconds for a single request "
            "(including connection establishment, request sending and "
            "response reading) while crawling (default: %(default)s).",
        )
        parser.add_argument(
            "-P",
            "--pid-file",
            type=_abs_path,
            metavar="PATH",
            dest="path_pidfile",
            default=FED_CRAWL_STATION_DEFAULT_PATH_PIDFILE,
            help="Absolute path to PID file (default: %(default)s).",
        )
        parser.add_argument(
            "--logging-conf",
            dest="path_logging_conf",
            metavar="PATH",
            default=FED_CRAWL_STATION_DEFAULT_PATH_LOGGING_CONF,
            help="Path to logging configuration file.",
        )

        return parser

    def _setup_logger(self, path_logging_conf=None, capture_warnings=False):
        """
        Initialize the logger of the application.
        """
        logging.basicConfig(level=logging.WARNING)

        LOGGER = FED_CRAWL_STATION_BASE_ID

        if path_logging_conf is not None:
            try:
                logging.config.fileConfig(path_logging_conf)
                logger = logging.getLogger(LOGGER)
                logger.info(
                    "Using logging configuration read from "
                    f"{path_logging_conf!r}."
                )
            except Exception as err:
                print(
                    f"WARNING: Setup logging failed for {path_logging_conf!r} "
                    f"with error: {err!r}."
                )
                logger = logging.getLogger(LOGGER)
        else:
            logger = logging.getLogger(LOGGER)
            logger.addHandler(logging.NullHandler())

        logging.captureWarnings(bool(capture_warnings))

        return logger

    def _get_pid_lock(self, path_pidfile):
        pid_lock = InterProcessLock(path_pidfile)
        got_pid_lock = pid_lock.acquire(blocking=False)
        if not got_pid_lock:
            raise AlreadyCrawling(path_pidfile)

        self.logger.debug(f"Aquired PID lock {self.config['path_pidfile']!r}")

        return pid_lock, got_pid_lock


# ----------------------------------------------------------------------------
def main():
    """
    main function for EIDA stationlite harvesting
    """

    app = CrawlFDSNWSStationApp()

    try:
        _ = app.config
    except AppError as err:
        # handle errors during the application configuration
        print(
            'ERROR: Application configuration failed "%s".' % err,
            file=sys.stderr,
        )
        sys.exit(ExitCodes.EXIT_ERROR)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(app.run())


# -----------------------------------------------------------------------------
if __name__ == "__main__":
    main()
