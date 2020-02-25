# -*- coding: utf-8 -*-
import aiohttp
import functools

from collections import OrderedDict
from copy import deepcopy
from urllib.parse import urlparse, urlunparse

from eidaws.federator.utils.misc import HelperGETRequest
from eidaws.utils.error import Error
from eidaws.utils.misc import convert_sncl_dicts_to_query_params
from eidaws.utils.schema import StreamEpochSchema
from eidaws.utils.settings import FDSNWS_QUERY_METHOD_TOKEN


def _query_params_from_stream_epochs(stream_epochs):

    serializer = StreamEpochSchema(
        many=True, context={'request': HelperGETRequest})

    return convert_sncl_dicts_to_query_params(
        serializer.dump(stream_epochs))


# -----------------------------------------------------------------------------
# NOTE(damb): RequestError instances carry the response, too.
class RequestsError(aiohttp.ClientError, Error):
    """Base request error ({})."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class ClientError(RequestsError):
    """Response code not OK ({})."""


class NoContent(RequestsError):
    """The request '{}' is returning no content ({})."""


# -----------------------------------------------------------------------------
class RequestHandlerBase:
    """
    RequestHandler base class implementation.
    """
    DEFAULT_HEADERS = {
               # force no encoding, because eida-federator currently cannot
               # handle this
               "Accept-Encoding": ""}

    def __init__(self, url, stream_epochs=[], query_params={}, headers={}):
        """
        :param url: URL
        :type url: str or bytes
        :param list stream_epochs: List of
            :py:class:`eidaws.utils.sncl.StreamEpoch` objects
        :param dict query_params: Dictionary of query parameters
        :param dict headers: Dictionary of request header parameters
        """

        if isinstance(url, bytes):
            url = url.decode('utf-8')
        url = urlparse(url)
        self._scheme = url.scheme
        self._netloc = url.netloc
        self._path = url.path.rstrip(
            FDSNWS_QUERY_METHOD_TOKEN).rstrip('/')

        self._query_params = OrderedDict(
            (p, v) for p, v in query_params.items()
            if self._filter_query_params(p, v))
        self._stream_epochs = stream_epochs

        self._headers = headers or self.DEFAULT_HEADERS

    @property
    def url(self):
        """
        Returns request URL without query parameters.
        """
        return urlunparse(
            (self._scheme,
             self._netloc,
             '{}/{}'.format(self._path, FDSNWS_QUERY_METHOD_TOKEN),
             '',
             '',
             ''))

    @property
    def stream_epochs(self):
        return self._stream_epochs

    @property
    def payload_post(self):
        raise NotImplementedError

    @property
    def payload_get(self):
        raise NotImplementedError

    def post(self, session):
        """
        :param session: Session the request will be bound to
        :type session: :py:class:`aiohttp.ClientSession`
        """
        raise NotImplementedError

    def get(self, session):
        """
        :param session: Session the request will be bound to
        :type session: :py:class:`aiohttp.ClientSession`
        """
        raise NotImplementedError

    def __str__(self):
        return ', '.join(["scheme={}".format(self._scheme),
                          "netloc={}".format(self._netloc),
                          "path={}.".format(self._path),
                          "qp={}".format(self._query_params),
                          "streams={}".format(
                              ', '.join(str(se)
                                        for se in self._stream_epochs))])

    def __repr__(self):
        return '<{}: {}>'.format(type(self).__name__, self)

    def _filter_query_params(self, param, value):
        return True


class RoutingRequestHandler(RequestHandlerBase):
    """
    Representation of a `eidaws-routing` (*StationLite*) request handler.
    """

    QUERY_PARAMS = set(('service',
                        'level',
                        'minlatitude', 'minlat',
                        'maxlatitude', 'maxlat',
                        'minlongitude', 'minlon',
                        'maxlongitude', 'maxlon'))

    def __init__(self, url, stream_epochs=[], query_params={}, headers={},
                 **kwargs):
        """
        :param str proxy_netloc: Force StationLite to prefix URLs with a proxy
            network location
        :param str access: Specifies the ``access`` query parameter when
            requesting data from StationLite
        """

        super().__init__(url, stream_epochs, query_params, headers)

        self._query_params['format'] = 'post'

        if 'proxy_netloc' in kwargs and kwargs['proxy_netloc'] is not None:
            self._query_params['proxynetloc'] = kwargs['proxy_netloc']

        self._query_params['access'] = kwargs.get('access', 'any')

    @property
    def payload_post(self):
        data = '\n'.join('{}={}'.format(p, v)
                         for p, v in self._query_params.items())

        return '{}\n{}'.format(
            data, '\n'.join(str(se) for se in self._stream_epochs))

    @property
    def payload_get(self):
        qp = deepcopy(self._query_params)
        qp.update(_query_params_from_stream_epochs(self._stream_epochs))
        return qp

    def post(self, session):
        return functools.partial(session.post, self.url,
                                 data=self.payload_post, headers=self._headers)

    def get(self, session):
        return functools.partial(session.get, self.url,
                                 params=self.payload_get,
                                 headers=self._headers)

    def _filter_query_params(self, param, value):
        return param in self.QUERY_PARAMS


class FdsnRequestHandler(RequestHandlerBase):

    QUERY_PARAMS = set(('service',
                        'nodata',
                        'minlatitude', 'minlat',
                        'maxlatitude', 'maxlat',
                        'minlongitude', 'minlon',
                        'maxlongitude', 'maxlon'))

    @property
    def format(self):
        try:
            return self._query_params['format']
        except KeyError:
            return None

    @format.setter
    def format(self, value):
        self._query_params['format'] = value

    @property
    def payload_post(self):
        data = '\n'.join('{}={}'.format(p, v)
                         for p, v in self._query_params.items())

        return '{}\n{}'.format(
            data, '\n'.join(str(se) for se in self._stream_epochs))

    @property
    def payload_get(self):
        qp = deepcopy(self._query_params)
        qp.update(_query_params_from_stream_epochs(self._stream_epochs))
        return qp

    def get(self, session):
        return functools.partial(session.get, self.url,
                                 params=self.payload_get,
                                 headers=self._headers)

    def post(self, session):
        return functools.partial(session.post, self.url,
                                 data=self.payload_post, headers=self._headers)

    def _filter_query_params(self, param, value):
        return param not in self.QUERY_PARAMS
