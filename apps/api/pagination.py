"""Pagination classes for the REST layer."""
from collections import OrderedDict

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardResultsPagination(PageNumberPagination):
    """Default pagination: ``?page=2&page_size=40``.

    The extra ``page`` / ``total_pages`` keys let a mobile client render a
    pager without recomputing anything from ``next``/``previous`` URLs.
    """

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            OrderedDict(
                [
                    ("count", self.page.paginator.count),
                    ("page", self.page.number),
                    ("page_size", self.get_page_size(self.request)),
                    ("total_pages", self.page.paginator.num_pages),
                    ("next", self.get_next_link()),
                    ("previous", self.get_previous_link()),
                    ("results", data),
                ]
            )
        )

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "example": 137},
                "page": {"type": "integer", "example": 1},
                "page_size": {"type": "integer", "example": 20},
                "total_pages": {"type": "integer", "example": 7},
                "next": {"type": "string", "nullable": True, "format": "uri"},
                "previous": {"type": "string", "nullable": True, "format": "uri"},
                "results": schema,
            },
        }


class LargeResultsPagination(StandardResultsPagination):
    """For endpoints that feed dropdowns / charts."""

    page_size = 100
    max_page_size = 500
