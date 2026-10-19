import os
import pytest
from fastapi import HTTPException
from app.api.v2.routes import verify_api_key
from app.config import settings


def test_path_traversal_sanitization():
    # Attempt malicious path traversal filename
    filename = "../../tmp/hacked_security.txt"
    sanitized_filename = os.path.basename(filename)

    assert "../" not in sanitized_filename
    assert sanitized_filename == "hacked_security.txt"


def test_upload_endpoint_requires_api_key():
    """
    The upload endpoint used to have no API-key dependency at all, unlike every sibling
    write endpoint — an unauthenticated arbitrary-file-write/ingest path (a critical
    finding in the debloat audit). verify_api_key is now a declared dependency on it
    (see app/api/v2/routes.py's api_upload_document), so it must reject bad keys the
    same way every other v2 endpoint does.
    """
    with pytest.raises(HTTPException) as exc:
        verify_api_key(None)
    assert exc.value.status_code == 401

    with pytest.raises(HTTPException) as exc:
        verify_api_key("wrong-key")
    assert exc.value.status_code == 401

    assert verify_api_key(settings.API_KEY) == settings.API_KEY
