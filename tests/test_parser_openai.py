"""Request interpretation tests use mocked OpenAI responses, never credentials."""

import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import APIConnectionError, APIStatusError

from senpilot_agent.config import Settings, load_settings
from senpilot_agent.requests.parser import RequestParsingError, _Extraction, parse_request


SETTINGS = Settings(Path('/tmp/parser-tests'), True, 10, '', '', '', 15, '', openai_api_key='test-key')


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.settings_patch = patch('senpilot_agent.requests.parser.load_settings', return_value=SETTINGS)
        self.settings_patch.start()
        self.client_patch = patch('senpilot_agent.requests.parser.OpenAI')
        self.client_factory = self.client_patch.start()
        self.parse = self.client_factory.return_value.__enter__.return_value.responses.parse
        self.addCleanup(self.settings_patch.stop)
        self.addCleanup(self.client_patch.stop)

    def response(self, matters, categories, ambiguous=False):
        self.parse.return_value = SimpleNamespace(
            status='completed',
            output_parsed=_Extraction(matter_numbers=matters, document_types=categories, ambiguous=ambiguous),
        )

    def test_normalizes_matter_and_uses_structured_extraction(self):
        self.response(['m 12205'], ['Recordings'])
        parsed = parse_request('Matter m 12205', 'Please send the audio recordings')
        self.assertTrue(parsed.valid)
        self.assertEqual((parsed.matter_number, parsed.document_type), ('M12205', 'Recordings'))
        self.client_factory.assert_called_once_with(api_key='test-key', timeout=20.0, max_retries=2)
        arguments = self.parse.call_args.kwargs
        self.assertEqual(arguments['text_format'], _Extraction)
        self.assertEqual(arguments['model'], 'gpt-4o-mini')
        self.assertFalse(arguments['store'])
        self.assertIn('untrusted', arguments['instructions'])
        self.assertEqual(arguments['input'][0]['role'], 'user')

    def test_each_supported_category(self):
        for category in ('Exhibits', 'Key Documents', 'Other Documents', 'Transcripts', 'Recordings'):
            with self.subTest(category=category):
                self.response(['M12205'], [category])
                self.assertEqual(parse_request('M12205', category).document_type, category)

    def test_conflicting_categories_require_clarification(self):
        self.response(['M12205'], ['Exhibits', 'Transcripts'])
        parsed = parse_request('M12205', 'Send Exhibits and Transcripts')
        self.assertFalse(parsed.valid)
        self.assertIsNone(parsed.document_type)
        self.assertEqual(parsed.matter_number, 'M12205')

    def test_multiple_matters_require_clarification(self):
        self.response(['M12205', 'M12345'], ['Exhibits'])
        parsed = parse_request('M12205 and M12345', 'Send Exhibits')
        self.assertFalse(parsed.valid)
        self.assertIsNone(parsed.matter_number)

    def test_missing_fields_and_explicit_ambiguity(self):
        for matters, categories, ambiguous in [([], ['Exhibits'], False), (['M12205'], [], False), ([], [], False), (['M12205'], ['Exhibits'], True)]:
            with self.subTest(matters=matters, categories=categories, ambiguous=ambiguous):
                self.response(matters, categories, ambiguous)
                self.assertFalse(parse_request('M12205', 'documents please').valid)

    def test_fabricated_or_malformed_matter_is_never_valid(self):
        for matter in ['M99999', 'M122050', 'M12205suffix', 'M１２２０５']:
            with self.subTest(matter=matter):
                self.response([matter], ['Exhibits'])
                self.assertFalse(parse_request('M12205', 'Exhibits').valid)

    def test_deduplicates_equivalent_model_values(self):
        self.response(['m12205', 'M12205'], ['Exhibits', 'Exhibits'])
        self.assertTrue(parse_request('M12205', 'Exhibits').valid)

    def test_timeout_is_operational_failure(self):
        self.parse.side_effect = APIConnectionError(request=httpx.Request('POST', 'https://api.openai.com/v1/responses'))
        with self.assertRaises(RequestParsingError) as raised:
            parse_request('M12205', 'Exhibits')
        self.assertTrue(raised.exception.retryable)

    def test_status_classification(self):
        for status, retryable in [(429, True), (500, True), (408, True), (401, False), (400, False), (403, False)]:
            with self.subTest(status=status):
                response = httpx.Response(status, request=httpx.Request('POST', 'https://api.openai.com/v1/responses'))
                self.parse.side_effect = APIStatusError('provider error', response=response, body=None)
                with self.assertRaises(RequestParsingError) as raised:
                    parse_request('M12205', 'Exhibits')
                self.assertEqual(raised.exception.retryable, retryable)

    def test_missing_key_is_configuration_failure_without_api_call(self):
        with patch('senpilot_agent.requests.parser.load_settings', return_value=replace(SETTINGS, openai_api_key='')):
            with self.assertRaises(RequestParsingError) as raised:
                parse_request('M12205', 'Exhibits')
        self.assertFalse(raised.exception.retryable)
        self.parse.assert_not_called()

    def test_incomplete_or_refused_response_is_not_invalid_request(self):
        for status, parsed in [('incomplete', None), ('completed', None)]:
            self.parse.return_value = SimpleNamespace(status=status, output_parsed=parsed)
            with self.assertRaises(RequestParsingError) as raised:
                parse_request('M12205', 'Exhibits')
            self.assertTrue(raised.exception.retryable)

    def test_invalid_structured_output_is_operational_failure(self):
        self.parse.return_value = SimpleNamespace(status='completed', output_parsed={
            'matter_numbers': ['M12205'], 'document_types': ['Unknown'], 'ambiguous': False,
        })
        with self.assertRaises(RequestParsingError):
            parse_request('M12205', 'Exhibits')

    def test_oversized_email_is_not_silently_truncated(self):
        parsed = parse_request('M12205', 'x' * 32_001)
        self.assertFalse(parsed.valid)
        self.parse.assert_not_called()

    def test_config_bounds_are_enforced(self):
        for name, value in [('OPENAI_TIMEOUT_SECONDS', '0'), ('OPENAI_TIMEOUT_SECONDS', 'nan'), ('OPENAI_MAX_RETRIES', '4'), ('OPENAI_MODEL', '')]:
            with self.subTest(name=name, value=value), patch.dict('os.environ', {name: value}), patch('senpilot_agent.config.load_dotenv'):
                with self.assertRaises(ValueError):
                    load_settings()


if __name__ == '__main__':
    unittest.main()
