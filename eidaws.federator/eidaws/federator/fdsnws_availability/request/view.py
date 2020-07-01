# -*- coding: utf-8 -*-

from eidaws.federator.settings import FED_AVAILABILITY_REQUEST_SERVICE_ID
from eidaws.federator.fdsnws_availability.request.parser import (
    AvailabilityExtentSchema,
    AvailabilityQuerySchema,
)
from eidaws.federator.fdsnws_availability.request.process import (
    AvailabilityQueryRequestProcessor,
    AvailabilityExtentRequestProcessor,
)
from eidaws.federator.fdsnws_availability.view import AvailabilityView

AvailabilityQueryView = AvailabilityView(
    FED_AVAILABILITY_REQUEST_SERVICE_ID,
    AvailabilityQuerySchema,
    AvailabilityQueryRequestProcessor,
)

AvailabilityExtentView = AvailabilityView(
    FED_AVAILABILITY_REQUEST_SERVICE_ID,
    AvailabilityExtentSchema,
    AvailabilityExtentRequestProcessor,
)
