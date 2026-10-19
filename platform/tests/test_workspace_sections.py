"""Menu ownership contracts for the existing PoC controls."""
from collections import Counter
from pathlib import Path
import unittest
from lxml import html

PLATFORM = Path(__file__).resolve().parents[1]


class WorkspaceSections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = html.fromstring((PLATFORM / 'index.html').read_text(encoding='utf8'))

    def owner(self, identifier):
        node = self.root.get_element_by_id(identifier)
        while node is not None:
            if node.get('data-workspace-page'):
                return node.get('data-workspace-page')
            node = node.getparent()
        return None

    def test_each_control_has_one_semantic_workspace(self):
        expected = {
            'application': ['app-form', 'natural-request', 'profile-summary', 'profile-llm'],
            'infrastructure': ['candidates', 'selected-summary', 'make-plan', 'resources-refresh', 'resource-issues'],
            'planning': ['plan-output', 'planning-spec', 'decision-agent', 'agents-save'],
            'deployment': ['app-submit', 'app-stop', 'app-refresh', 'cloud-panel', 'cloud-campaign-panel'],
            'operation': ['app-measure', 'feedback-output', 'prometheus-panel', 'replica-panel', 'operation-agent', 'operation-agents-save', 'operation-history', 'monitor-return-current'],
            'validation': ['experiment-result', 'experiment-result-refresh', 'research-evaluation', 'llm-eval-panel', 'planning-comparisons', 'operation-policy-comparison', 'validation-cards', 'policy-validation'],
            'settings': ['agent-register', 'agents-refresh'],
            'events': ['event-log', 'pipeline', 'result'],
        }
        for page, identifiers in expected.items():
            for identifier in identifiers:
                with self.subTest(control=identifier):
                    self.assertEqual(self.owner(identifier), page)

    def test_overview_contains_no_duplicate_experiment_result_cards(self):
        self.assertFalse(self.root.xpath('//*[@id="validation-overview" or @id="policy-validation-overview"]'))
        self.assertEqual(len(self.root.xpath('//nav[contains(@class,"sidebar-nav")]/a')), 9)

    def test_result_summary_precedes_collapsed_research_and_agent_settings(self):
        current = self.root.get_element_by_id('experiment-result')
        research = self.root.get_element_by_id('research-evaluation')
        self.assertEqual(current.getparent().get('id'), 'validation')
        self.assertEqual(research.tag, 'details')
        self.assertNotIn('open', research.attrib)
        self.assertLess(list(current.getparent()).index(current), list(research.getparent()).index(research))
        self.assertIn('실험 결과', self.root.get_element_by_id('validation-title').text_content())
        for identifier in ['validation-cards', 'policy-validation', 'llm-eval-panel', 'planning-comparisons']:
            self.assertIn(research, list(self.root.get_element_by_id(identifier).iterancestors()))

    def test_live_workspace_does_not_require_saving_another_roles_selection(self):
        for page in ['planning', 'operation']:
            section = self.root.get_element_by_id(page)
            self.assertNotIn('두 역할 선택 저장', section.text_content())
        self.assertIn('operation-agent-status', [n.get('id') for n in self.root.iter()])

    def test_extra_vm_campaign_is_accessible_outside_single_app_cloud_panel(self):
        self.assertEqual(self.root.get_element_by_id('cloud-campaign-panel').getparent().get('id'), 'deployment')

    def test_controls_and_hash_targets_remain_unique_and_resolvable(self):
        identifiers = [n.get('id') for n in self.root.iter() if n.get('id')]
        self.assertEqual([key for key, count in Counter(identifiers).items() if count > 1], [])
        for anchor in self.root.xpath('//a[starts-with(@href,"#")]'):
            self.assertIn(anchor.get('href')[1:], identifiers)
        for tab in self.root.xpath('//*[@role="tab"]'):
            self.assertIn(tab.get('aria-controls'), identifiers)


if __name__ == '__main__':
    unittest.main()
