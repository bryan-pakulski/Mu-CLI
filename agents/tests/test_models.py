import json
import unittest
from unittest import mock

from mu_cli.models import get_models


class ModelCatalogTests(unittest.TestCase):
    @mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
    @mock.patch("mu_cli.models.request.urlopen")
    def test_gemini_models_are_discovered_dynamically(self, mock_urlopen: mock.Mock) -> None:
        mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            {
                "models": [
                    {
                        "name": "models/gemini-3.0-pro",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/gemini-3.0-flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/embedding-001",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            }
        ).encode("utf-8")

        models = get_models("gemini")
        self.assertEqual(["gemini-3.0-flash", "gemini-3.0-pro"], models)

    @mock.patch.dict("os.environ", {}, clear=True)
    def test_gemini_models_fallback_without_api_key(self) -> None:
        models = get_models("gemini")
        self.assertIn("gemini-3.1-pro-preview", models)


if __name__ == "__main__":
    unittest.main()
