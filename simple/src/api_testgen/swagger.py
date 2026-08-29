"""OpenAPI/Swagger spec parser for JSONPlaceholder."""

from __future__ import annotations

from api_testgen.models import Endpoint


async def fetch_swagger_spec(base_url: str) -> dict:
    """Fetch OpenAPI spec from a service.

    JSONPlaceholder doesn't have a real /openapi.json endpoint,
    so we build a hardcoded spec for it.
    """
    # JSONPlaceholder doesn't serve OpenAPI spec, so we define it inline.
    return _jsonplaceholder_spec()


def _jsonplaceholder_spec() -> dict:
    """Hardcoded OpenAPI spec for jsonplaceholder.typicode.com."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "JSONPlaceholder", "version": "1.0"},
        "paths": {
            "/posts": {
                "get": {
                    "summary": "Get all posts",
                    "responses": {
                        "200": {
                            "description": "Array of post objects",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "userId": {"type": "integer"},
                                                "id": {"type": "integer"},
                                                "title": {"type": "string"},
                                                "body": {"type": "string"},
                                            },
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
                "post": {
                    "summary": "Create a post",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string"},
                                        "body": {"type": "string"},
                                        "userId": {"type": "integer"},
                                    },
                                }
                            }
                        }
                    },
                    "responses": {
                        "201": {
                            "description": "Created post",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "title": {"type": "string"},
                                            "body": {"type": "string"},
                                            "userId": {"type": "integer"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
            },
            "/posts/{id}": {
                "get": {
                    "summary": "Get a post by ID",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Post object",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "userId": {"type": "integer"},
                                            "id": {"type": "integer"},
                                            "title": {"type": "string"},
                                            "body": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
                "put": {
                    "summary": "Update a post",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string"},
                                        "body": {"type": "string"},
                                        "userId": {"type": "integer"},
                                    },
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Updated post",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "title": {"type": "string"},
                                            "body": {"type": "string"},
                                            "userId": {"type": "integer"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
                "delete": {
                    "summary": "Delete a post",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Empty object",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        }
                    },
                },
            },
            "/posts/{id}/comments": {
                "get": {
                    "summary": "Get comments for a post",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Array of comment objects",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "postId": {"type": "integer"},
                                                "id": {"type": "integer"},
                                                "name": {"type": "string"},
                                                "email": {"type": "string"},
                                                "body": {"type": "string"},
                                            },
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
            },
            "/comments": {
                "get": {
                    "summary": "Get all comments",
                    "responses": {
                        "200": {
                            "description": "Array of comment objects",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "postId": {"type": "integer"},
                                                "id": {"type": "integer"},
                                                "name": {"type": "string"},
                                                "email": {"type": "string"},
                                                "body": {"type": "string"},
                                            },
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
            },
            "/users": {
                "get": {
                    "summary": "Get all users",
                    "responses": {
                        "200": {
                            "description": "Array of user objects",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "name": {"type": "string"},
                                                "username": {"type": "string"},
                                                "email": {"type": "string"},
                                            },
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
            },
            "/users/{id}": {
                "get": {
                    "summary": "Get a user by ID",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "User object",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "name": {"type": "string"},
                                            "username": {"type": "string"},
                                            "email": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
            },
        },
    }


def parse_endpoints(spec: dict) -> list[Endpoint]:
    """Extract endpoints from OpenAPI spec."""
    endpoints: list[Endpoint] = []
    for path, methods in spec.get("paths", {}).items():
        for method, details in methods.items():
            if method in ("get", "post", "put", "patch", "delete"):
                request_schema = None
                if "requestBody" in details:
                    content = details["requestBody"].get("content", {})
                    json_content = content.get("application/json", {})
                    request_schema = json_content.get("schema")

                response_schema = None
                response_codes = []
                for code, resp in details.get("responses", {}).items():
                    response_codes.append(code)
                    if "content" in resp:
                        json_resp = resp["content"].get("application/json", {})
                        response_schema = json_resp.get("schema")

                endpoints.append(
                    Endpoint(
                        method=method.upper(),
                        path=path,
                        summary=details.get("summary", ""),
                        request_body_schema=request_schema,
                        response_schema=response_schema,
                        response_codes=response_codes,
                    )
                )
    return endpoints
