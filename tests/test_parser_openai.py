"""Request interpretation tests use mocked OpenAI responses, never credentials."""

import unittest
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import APIConnectionError, APIStatusError

from senpilot_agent.config import Settings, load_settings
from senpilot_agent.requests.parser import ClarificationContext, RequestParsingError, _Extraction, parse_request


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

    def response(self, matters, categories, ambiguous=False, ambiguity_reason='none',
                 per_matter=None, summary_only=False):
        mapped = [dict({'summary_only': False}, **item) for item in (per_matter or [])]
        self.parse.return_value = SimpleNamespace(
            status='completed',
            output_parsed=_Extraction(
                matter_numbers=matters, document_types=categories, ambiguous=ambiguous,
                ambiguity_reason=ambiguity_reason, matter_document_types=mapped,
                summary_only=summary_only,
            ),
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

    def test_multiple_document_types_are_shared(self):
        self.response(['M12205'], ['Transcripts', 'Exhibits'])
        parsed = parse_request('M12205', 'Send Exhibits and Transcripts')
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.document_type, 'Exhibits, Transcripts')
        self.assertEqual(parsed.matter_number, 'M12205')

    def test_matter_without_document_type_is_valid(self):
        self.response(['M12205'], [])
        parsed = parse_request('M12205', 'What is the status of this matter?')
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.document_type, '')
        self.assertEqual(parsed.matters, ('M12205',))
        self.assertFalse(parsed.wants_summary_only('M12205'))

    def test_count_question_is_summary_only_even_when_category_is_mentioned(self):
        self.response(['M12205'], ['Exhibits'], summary_only=True)
        parsed = parse_request('Exhibit count', 'How many Exhibits are there for M12205? Do not send files.')
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.selections, (('M12205', ''),))
        self.assertTrue(parsed.wants_summary_only('M12205'))

    def test_summary_and_download_can_differ_by_matter(self):
        self.response(['M12205', 'M12345'], [], per_matter=[
            {'matter_number': 'M12205', 'document_types': ['Exhibits'], 'summary_only': True},
            {'matter_number': 'M12345', 'document_types': ['Transcripts']},
        ])
        parsed = parse_request('M12205 and M12345', 'Count exhibits for M12205; send transcripts for M12345')
        self.assertEqual(parsed.selections, (('M12205', ''), ('M12345', 'Transcripts')))
        self.assertTrue(parsed.wants_summary_only('M12205'))
        self.assertFalse(parsed.wants_summary_only('M12345'))

    def test_multiple_matters_share_one_category(self):
        self.response(['M12205', 'M12345'], ['Exhibits'])
        parsed = parse_request('M12205 and M12345', 'Send Exhibits')
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.matters, ('M12205', 'M12345'))
        self.assertEqual(parsed.document_type, 'Exhibits')

    def test_missing_fields_and_explicit_ambiguity(self):
        for matters, categories, ambiguous in [([], ['Exhibits'], False), ([], [], False), (['M12205'], ['Exhibits'], True)]:
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

    def test_multiple_matters_normalize_and_deduplicate(self):
        self.response(['m 12205', 'M12345', 'M12205'], ['Exhibits'])
        parsed = parse_request('M12205 and m 12345', 'Exhibits')
        self.assertEqual(parsed.matters, ('M12205', 'M12345'))

    def test_one_fabricated_matter_rejects_entire_batch(self):
        self.response(['M12205', 'M99999'], ['Exhibits'])
        self.assertFalse(parse_request('M12205 and M12345', 'Exhibits').valid)

    def test_multiple_categories_apply_to_every_listed_matter(self):
        self.response(['M12205', 'M12345'], ['Exhibits', 'Transcripts'])
        parsed = parse_request('M12205 and M12345', 'Exhibits and Transcripts')
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.matters, ('M12205', 'M12345'))
        self.assertEqual(parsed.document_type, 'Exhibits, Transcripts')

    def test_different_types_per_matter_are_kept_separate(self):
        self.response(['M12205', 'M12345'], [], per_matter=[
            {'matter_number': 'M12205', 'document_types': ['Exhibits']},
            {'matter_number': 'M12345', 'document_types': ['Transcripts']},
        ])
        parsed = parse_request('M12205 exhibits; M12345 transcripts', '')
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.selections, (('M12205', 'Exhibits'), ('M12345', 'Transcripts')))

    def test_redundant_shared_mapping_without_matter_numbers_is_normalized(self):
        self.response([], ['Key Documents'], per_matter=[
            {'matter_number': 'M12205', 'document_types': ['Key Documents']},
            {'matter_number': 'M12345', 'document_types': ['Key Documents']},
        ])
        parsed = parse_request('Shared documents', 'Send Key Documents for M12205 and M12345')
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.selections, (
            ('M12205', 'Key Documents'), ('M12345', 'Key Documents'),
        ))

    def test_conflicting_shared_and_per_matter_types_require_clarification(self):
        self.response(['M12205', 'M12345'], ['Key Documents'], per_matter=[
            {'matter_number': 'M12205', 'document_types': ['Key Documents']},
            {'matter_number': 'M12345', 'document_types': ['Transcripts']},
        ])
        parsed = parse_request('M12205 and M12345', 'Send Key Documents for M12205 and Transcripts for M12345')
        self.assertFalse(parsed.valid)

    def test_incomplete_per_matter_mapping_requires_clarification(self):
        self.response(['M12205', 'M12345'], [], per_matter=[
            {'matter_number': 'M12205', 'document_types': ['Exhibits']},
        ])
        parsed = parse_request('M12205 exhibits; M12345 transcripts', '')
        self.assertFalse(parsed.valid)
        self.assertIn('which document types', parsed.error)

    def test_all_documents_is_a_clear_request_for_five_types(self):
        self.response(['M12205'], list(('Exhibits', 'Key Documents', 'Other Documents', 'Transcripts', 'Recordings')))
        parsed = parse_request('All documents for M12205', '')
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.selections[0][1],
                         'Exhibits, Key Documents, Other Documents, Transcripts, Recordings')

    def test_ambiguous_matter_gets_specific_question(self):
        self.response(['M12205', 'M12345'], ['Key Documents'],
                      ambiguous=True, ambiguity_reason='matter_selection')
        parsed = parse_request('Key Documents for M12205 or M12345?', '')
        self.assertEqual(parsed.error, 'Which matter number would you like me to use: M12205, M12345?')

    def test_followup_supplies_prior_clarification_and_keeps_selected_type(self):
        self.response(['M12205'], ['Key Documents'])
        context = ClarificationContext(
            '', 'Please send Key Documents for M12205 or M12345.',
            'Which matter number would you like me to use: M12205, M12345?',
            'M12205', 'Key Documents',
        )
        parsed = parse_request('', 'Please use M12205 and send only Key Documents.', context=context)
        self.assertEqual(parsed.selections, (('M12205', 'Key Documents'),))
        payload = json.loads(self.parse.call_args.kwargs['input'][0]['content'])
        self.assertEqual(payload['body'], 'Please use M12205 and send only Key Documents.')
        self.assertEqual(payload['prior_clarification']['body'], context.body)
        self.assertEqual(payload['prior_clarification']['question'], context.question)

    def test_short_clarification_answers_inherit_the_original_selection(self):
        context = ClarificationContext(
            '', 'Please send Key Documents for M12205 or M12345.',
            'Which matter number would you like me to use: M12205, M12345?',
            'M12205', 'Key Documents',
        )
        for reply, expected in [('M12205', 'M12205'), ('M12345', 'M12345'), ('the first one', 'M12205')]:
            with self.subTest(reply=reply):
                self.assertEqual(parse_request('', reply, context=context).selections,
                                 ((expected, 'Key Documents'),))
        self.parse.assert_not_called()

    def test_document_type_clarification_inherits_matter(self):
        context = ClarificationContext(
            '', 'Send Exhibits or Transcripts for M12205',
            'Which document types would you like for M12205? Choose one or more of: Exhibits, Transcripts.',
            'M12205', 'Exhibits, Transcripts',
        )
        self.assertEqual(parse_request('', 'Transcripts', context=context).selections,
                         (('M12205', 'Transcripts'),))
        self.parse.assert_not_called()

    def test_followup_still_rejects_unmentioned_matter(self):
        self.response(['M99999'], ['Key Documents'])
        context = ClarificationContext('', 'Key Documents for M12205 or M12345?', 'Which matter?')
        self.assertFalse(parse_request('', 'the first one', context=context).valid)

    def test_ambiguous_document_type_gets_specific_question(self):
        self.response(['M12205'], ['Exhibits', 'Transcripts'], ambiguous=True,
                      ambiguity_reason='document_selection')
        parsed = parse_request('Send Exhibits or Transcripts for M12205', '')
        self.assertIn('Which document types would you like for M12205?', parsed.error)
        self.assertIn('Exhibits, Key Documents', parsed.error)

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
            'ambiguity_reason': 'none', 'matter_document_types': [], 'summary_only': False,
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
