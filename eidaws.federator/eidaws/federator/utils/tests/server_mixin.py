# -*- coding: utf-8 -*-

import pytest

from aiohttp import web

# TODO(damb): Test if routing returns 500, ClientError etc


class _TestRoutingMixin:
    """
    Routing specific tests for test classes providing both the properties
    ``FED_PATH_QUERY`` and ``PATH_QUERY`` and a ``create_app`` method.
    """

    @pytest.mark.parametrize(
        "method,params_or_data",
        [
            (
                "GET",
                {
                    "net": "CH",
                    "sta": "FOO",
                    "loc": "--",
                    "cha": "LHZ",
                    "start": "2019-01-01",
                    "end": "2019-01-05",
                },
            ),
            ("POST", b"CH FOO -- LHZ 2019-01-01 2019-01-05",),
        ],
    )
    async def test_no_route(
        self,
        make_federated_eida,
        eidaws_routing_path_query,
        method,
        params_or_data,
    ):
        mocked_routing = {
            "localhost": [
                (eidaws_routing_path_query, method, web.Response(status=204,),)
            ]
        }
        client, faked_routing, faked_endpoints = await make_federated_eida(
            self.create_app(), mocked_routing_config=mocked_routing,
        )

        method = method.lower()
        kwargs = {"params" if method == "get" else "data": params_or_data}
        resp = await getattr(client, method)(self.FED_PATH_QUERY, **kwargs)

        assert resp.status == 204

        faked_routing.assert_no_unused_routes()

    @pytest.mark.parametrize(
        "method,params_or_data",
        [
            (
                "GET",
                {
                    "net": "CH",
                    "sta": "FOO",
                    "loc": "--",
                    "cha": "LHZ",
                    "start": "2019-01-01",
                    "end": "2019-01-05",
                },
            ),
            ("POST", b"CH FOO -- LHZ 2019-01-01 2019-01-05",),
        ],
    )
    async def test_no_data(
        self,
        make_federated_eida,
        eidaws_routing_path_query,
        method,
        params_or_data,
    ):
        mocked_routing = {
            "localhost": [
                (
                    eidaws_routing_path_query,
                    method,
                    web.Response(
                        status=200,
                        text=(
                            "http://eida.ethz.ch" + self.PATH_QUERY + "\n"
                            "CH FOO -- LHZ 2019-01-01T00:00:00 2019-01-05T00:00:00\n"
                        ),
                    ),
                )
            ]
        }

        mocked_endpoints = {
            "eida.ethz.ch": [
                (self.PATH_QUERY, "GET", web.Response(status=204,),),
            ]
        }

        client, faked_routing, faked_endpoints = await make_federated_eida(
            self.create_app(),
            mocked_routing_config=mocked_routing,
            mocked_endpoint_config=mocked_endpoints,
        )

        method = method.lower()
        kwargs = {"params" if method == "get" else "data": params_or_data}
        resp = await getattr(client, method)(self.FED_PATH_QUERY, **kwargs)

        assert resp.status == 204

        faked_routing.assert_no_unused_routes()
        faked_endpoints.assert_no_unused_routes()


class _TestKeywordParserMixin:
    """
    Keyword parser specific tests for test classes providing both the property
    ``FED_PATH_QUERY`` and a ``create_app`` method.
    """

    @pytest.mark.parametrize(
        "method,params_or_data",
        [
            ("GET", {"foo": "bar"},),
            ("POST", b"foo=bar\nCH HASLI -- LHZ 2019-01-01 2019-01-05",),
        ],
    )
    async def test_invalid_args(
        self,
        make_federated_eida,
        fdsnws_error_content_type,
        method,
        params_or_data,
    ):
        client, _, _ = await make_federated_eida(self.create_app())

        method = method.lower()
        kwargs = {"params" if method == "get" else "data": params_or_data}
        resp = await getattr(client, method)(self.FED_PATH_QUERY, **kwargs)

        assert resp.status == 400
        assert (
            f"ValidationError: Invalid request query parameters: {{'foo'}}"
            in await resp.text()
        )
        assert (
            "Content-Type" in resp.headers
            and resp.headers["Content-Type"] == fdsnws_error_content_type
        )

    async def test_post_empty(
        self, make_federated_eida, fdsnws_error_content_type,
    ):
        client, _, _ = await make_federated_eida(self.create_app())

        data = b""
        resp = await client.post(self.FED_PATH_QUERY, data=data)

        assert resp.status == 400
        assert (
            "Content-Type" in resp.headers
            and resp.headers["Content-Type"] == fdsnws_error_content_type
        )

    async def test_post_equal(
        self, make_federated_eida, fdsnws_error_content_type
    ):
        client, _, _ = await make_federated_eida(self.create_app())

        data = b"="
        resp = await client.post(self.FED_PATH_QUERY, data=data)

        assert resp.status == 400
        assert "ValidationError: RTFM :)." in await resp.text()
        assert (
            "Content-Type" in resp.headers
            and resp.headers["Content-Type"] == fdsnws_error_content_type
        )


class _TestCORSMixin:
    """
    CORS related tests for test classes providing both the property
    ``FED_PATH_QUERY`` and a ``create_app`` method.
    """

    @pytest.mark.parametrize(
        "method,params_or_data",
        [
            ("GET", {"foo": "bar"},),
            ("POST", b"foo=bar\nCH HASLI -- LHZ 2019-01-01 2019-01-05",),
        ],
    )
    async def test_get_cors_simple(
        self, make_federated_eida, method, params_or_data
    ):
        client, _, _ = await make_federated_eida(self.create_app())

        origin = "http://foo.example.com"

        method = method.lower()
        kwargs = {"params" if method == "get" else "data": params_or_data}
        resp = await getattr(client, method)(
            self.FED_PATH_QUERY, headers={"Origin": origin}, **kwargs
        )

        assert resp.status == 400
        assert (
            "Access-Control-Expose-Headers" in resp.headers
            and resp.headers["Access-Control-Expose-Headers"] == ""
        )
        assert (
            "Access-Control-Allow-Origin" in resp.headers
            and resp.headers["Access-Control-Allow-Origin"] == origin
        )

    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_cors_preflight(self, make_federated_eida, method):
        client, _, _ = await make_federated_eida(self.create_app())

        origin = "http://foo.example.com"
        headers = {"Origin": origin, "Access-Control-Request-Method": method}

        resp = await client.options(self.FED_PATH_QUERY, headers=headers)

        assert resp.status == 200
        assert (
            "Access-Control-Allow-Methods" in resp.headers
            and resp.headers["Access-Control-Allow-Methods"] == method
        )
        assert (
            "Access-Control-Allow-Origin" in resp.headers
            and resp.headers["Access-Control-Allow-Origin"] == origin
        )

    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_cors_preflight_forbidden(self, make_federated_eida, method):
        client, _, _ = await make_federated_eida(self.create_app())

        origin = "http://foo.example.com"

        resp = await client.options(
            self.FED_PATH_QUERY, headers={"Origin": origin}
        )
        assert resp.status == 403

        resp = await client.options(
            self.FED_PATH_QUERY,
            headers={"Access-Control-Request-Method": method},
        )
        assert resp.status == 403
