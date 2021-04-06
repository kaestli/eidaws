# -*- coding: utf-8 -*-

FDSNWS_QUERY_METHOD_TOKEN = "query"
FDSNWS_QUERYAUTH_METHOD_TOKEN = "queryauth"
FDSNWS_EXTENT_METHOD_TOKEN = "extent"
FDSNWS_EXTENTAUTH_METHOD_TOKEN = "extentauth"
FDSNWS_VERSION_METHOD_TOKEN = "version"

FDSNWS_STATION_MAJORVERSION = "1"
FDSNWS_STATION_PATH = "/".join(
    ["/fdsnws/station", FDSNWS_STATION_MAJORVERSION]
)
FDSNWS_STATION_PATH_QUERY = "/".join(
    [FDSNWS_STATION_PATH, FDSNWS_QUERY_METHOD_TOKEN]
)
FDSNWS_DATASELECT_MAJORVERSION = "1"
FDSNWS_DATASELECT_PATH = "/".join(
    ["/fdsnws/dataselect", FDSNWS_DATASELECT_MAJORVERSION]
)
FDSNWS_DATASELECT_PATH_QUERY = "/".join(
    [FDSNWS_DATASELECT_PATH, FDSNWS_QUERY_METHOD_TOKEN]
)
FDSNWS_DATASELECT_PATH_QUERYAUTH = "/".join(
    [FDSNWS_DATASELECT_PATH, FDSNWS_QUERYAUTH_METHOD_TOKEN]
)
FDSNWS_AVAILABILITY_MAJORVERSION = "1"
FDSNWS_AVAILABILITY_PATH = "/".join(
    ["/fdsnws/availability", FDSNWS_AVAILABILITY_MAJORVERSION]
)
FDSNWS_AVAILABILITY_PATH_QUERY = "/".join(
    [FDSNWS_AVAILABILITY_PATH, FDSNWS_QUERY_METHOD_TOKEN]
)
FDSNWS_AVAILABILITY_PATH_QUERYAUTH = "/".join(
    [FDSNWS_AVAILABILITY_PATH, FDSNWS_QUERYAUTH_METHOD_TOKEN]
)
FDSNWS_AVAILABILITY_PATH_EXTENT = "/".join(
    [FDSNWS_AVAILABILITY_PATH, FDSNWS_EXTENT_METHOD_TOKEN]
)
FDSNWS_AVAILABILITY_PATH_EXTENTAUTH = "/".join(
    [FDSNWS_AVAILABILITY_PATH, FDSNWS_EXTENTAUTH_METHOD_TOKEN]
)

EIDAWS_WFCATALOG_MAJORVERSION = "1"
EIDAWS_WFCATALOG_PATH = "/".join(
    ["/eidaws/wfcatalog", EIDAWS_WFCATALOG_MAJORVERSION]
)
EIDAWS_WFCATALOG_PATH_QUERY = "/".join(
    [EIDAWS_WFCATALOG_PATH, FDSNWS_QUERY_METHOD_TOKEN]
)
EIDAWS_ROUTING_MAJORVERSION = "1"
EIDAWS_ROUTING_PATH = "/".join(
    ["/eidaws/routing", EIDAWS_ROUTING_MAJORVERSION]
)
EIDAWS_ROUTING_PATH_QUERY = "/".join(
    [EIDAWS_ROUTING_PATH, FDSNWS_QUERY_METHOD_TOKEN]
)

FDSNWS_DEFAULT_NO_CONTENT_ERROR_CODE = 204
FDSNWS_NO_CONTENT_CODES = (FDSNWS_DEFAULT_NO_CONTENT_ERROR_CODE, 404)
FDSNWS_DOCUMENTATION_URI = "http://www.fdsn.org/webservices/"

FDSNWS_QUERY_VALUE_SEPARATOR_CHAR = "="
FDSNWS_QUERY_LIST_SEPARATOR_CHAR = ","
FDSNWS_QUERY_WILDCARD_MULT_CHAR = "*"
FDSNWS_QUERY_WILDCARD_SINGLE_CHAR = "?"

# ----------------------------------------------------------------------------
STATIONXML_NAMESPACES = ("{http://www.fdsn.org/xml/station/1}",)

STATIONXML_ELEMENT_NETWORK = "Network"
STATIONXML_ELEMENT_STATION = "Station"
STATIONXML_ELEMENT_CHANNEL = "Channel"


def _element_to_tags(element):
    return [f"{ns}{element}" for ns in STATIONXML_NAMESPACES]


STATIONXML_TAGS_NETWORK = _element_to_tags(STATIONXML_ELEMENT_NETWORK)
STATIONXML_TAGS_STATION = _element_to_tags(STATIONXML_ELEMENT_STATION)
STATIONXML_TAGS_CHANNEL = _element_to_tags(STATIONXML_ELEMENT_CHANNEL)


# ----------------------------------------------------------------------------
REQUEST_CONFIG_KEY = "eidaws"
KEY_REQUEST_ID = "request_id"
KEY_REQUEST_STARTTIME = "request_starttime"
KEY_REQUEST_QUERY_PARAMS = "query_params"
KEY_REQUEST_STREAM_EPOCHS = "stream_epochs"
