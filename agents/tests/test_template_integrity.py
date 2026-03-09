from pathlib import Path
import unittest


class TemplateIntegrityTests(unittest.TestCase):
    def test_notice_div_is_outside_script_block(self):
        template = Path('agents/mu_cli/templates/index.html').read_text(encoding='utf-8')
        notice = '<div id="notice" class="notice" role="status" aria-live="polite"></div>'
        notice_idx = template.find(notice)
        script_start = template.find('<script>')
        script_end = template.rfind('</script>')

        self.assertGreaterEqual(notice_idx, 0, 'notice div should exist in template')
        self.assertGreaterEqual(script_start, 0, 'inline script block should exist')
        self.assertGreater(script_end, script_start, 'inline script should be closed')
        self.assertLess(notice_idx, script_start, 'notice div must appear before inline script start')

        inline_js = template[script_start + len('<script>'):script_end]
        self.assertNotIn('<div id="notice"', inline_js, 'HTML notice div must not be embedded in JS')

    def test_infer_message_timestamps_block_is_not_corrupted(self):
        template = Path('agents/mu_cli/templates/index.html').read_text(encoding='utf-8')
        script_start = template.find('<script>')
        script_end = template.rfind('</script>')
        inline_js = template[script_start + len('<script>'):script_end]

        marker = 'function inferMessageTimestamps(messages, turns) {'
        start = inline_js.find(marker)
        self.assertGreaterEqual(start, 0, 'inferMessageTimestamps function should exist')

        end = inline_js.find('\n\nfunction renderApprovalArgs(args) {', start)
        self.assertGreater(end, start, 'inferMessageTimestamps should end before renderApprovalArgs')

        block = inline_js[start:end]
        self.assertNotIn('<div id="notice"', block, 'notice div must not appear inside inferMessageTimestamps')
        self.assertIn('return out;', block)
        self.assertIn('messages.forEach((m, idx) => {', block)


if __name__ == '__main__':
    unittest.main()
