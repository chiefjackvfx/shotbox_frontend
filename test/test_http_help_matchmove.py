from pathlib import Path
import sys
from unittest import mock

FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from http_help import DjangoAPI


def test_update_job_sends_matchmove_path_to_job_endpoint():
    api = DjangoAPI()
    payload = {"id": 7, "matchmove_path": "/shared/job_assets/matchmove"}
    response = mock.Mock()
    response.json.return_value = payload
    with mock.patch.object(api, "_request", return_value=response) as request:
        assert api.update_job(7, matchmove_path=payload["matchmove_path"]) == payload
    request.assert_called_once_with(
        "PATCH", f"{api.base_url}jobs/7",
        json={"matchmove_path": payload["matchmove_path"]}, operation="update_job",
    )
