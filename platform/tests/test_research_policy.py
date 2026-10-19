import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / 'research_policy.py'
if PATH.exists():
    spec = importlib.util.spec_from_file_location('research_policy', PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
else:
    module = None


class ResearchPolicyTests(unittest.TestCase):
    def require_module(self):
        self.assertIsNotNone(module, 'Original research policy connection is missing')

    def test_selection_is_bounded_per_model_and_independent_of_row_order(self):
        self.require_module()
        records = [{'source_scenario_id': f'{m}-{i}', 'model_id': m} for m in ['small', 'large'] for i in range(30)]
        selected = module.select_groups(records, limit=4)
        self.assertEqual(len(selected), 8)
        self.assertEqual(selected, module.select_groups(list(reversed(records)), limit=4))

    def test_small_placement_dataset_is_not_lost_under_large_lifecycle_dataset(self):
        self.require_module()
        records = [{'dataset': 'lifecycle', 'source_scenario_id': f'l-{i}', 'model_id': 'm'} for i in range(100)]
        records.append({'dataset': 'placement', 'source_scenario_id': 'small-original', 'model_id': 'm'})
        self.assertIn('small-original', module.select_groups(records, limit=2))

    def test_replay_excludes_infeasible_high_reward_and_does_not_call_it_live_scaling(self):
        self.require_module()
        class Policy:
            manifest = {'model_version': 'test', 'dataset_sha256': 'd', 'artifact_sha256': 'a', 'guard': {'minimum_confidence': .7, 'maximum_ood_ratio': .2}}
            def predict(self, features):
                return {'expected_utility': features['test_score'], 'confidence': .9, 'ood_ratio': 0}
        rows = [
            {'candidate_id': 'not-enough-memory', 'action': 'SCALE_OUT', 'desired_replicas': 2, 'feasible': False, 'reward': 1, 'features': {'test_score': 1}},
            {'candidate_id': 'measured', 'action': 'KEEP', 'desired_replicas': 1, 'feasible': True, 'reward': .8, 'features': {'test_score': .8}},
            {'candidate_id': 'measured', 'action': 'SCALE_OUT', 'desired_replicas': 2, 'feasible': True, 'reward': .7, 'features': {'test_score': .6}},
        ]
        case = {'case_id': 'sample', 'phase': 'operation', 'rows': rows, 'split': 'test', 'transition_evidence': 'paired_static_measurements'}
        result = module.replay(Policy(), case)
        self.assertEqual(result['proposal']['action'], 'KEEP')
        self.assertEqual(result['oracle']['candidate_id'], 'measured')
        self.assertTrue(result['agreement'])
        self.assertEqual(result['regret'], 0)
        self.assertFalse(result['execution_performed'])
        self.assertEqual(case['rows'], rows)
        baseline = result['comparison']['baseline']
        self.assertEqual(baseline['candidate_id'], 'not-enough-memory')
        self.assertTrue(baseline['constraint_violation'])
        self.assertFalse(baseline['agreement'])
        self.assertIsNone(baseline['regret'])
        validated = result['comparison']['validated']
        self.assertEqual(validated['action'], 'KEEP')
        self.assertFalse(validated['constraint_violation'])
        self.assertTrue(validated['agreement'])

    def test_replay_retains_abstention_instead_of_displaying_a_confident_winner(self):
        self.require_module()
        class Policy:
            manifest = {'guard': {'minimum_confidence': .7, 'maximum_ood_ratio': .2}}
            def predict(self, features):
                return {'expected_utility': .9, 'confidence': .6, 'ood_ratio': .1}
        result = module.replay(Policy(), {'case_id': 's', 'phase': 'planning', 'split': 'validation', 'rows': [{'candidate_id': 'c', 'action': 'DEPLOY', 'desired_replicas': 1, 'feasible': True, 'reward': .8, 'features': {}}]})
        self.assertEqual(result['proposal']['action'], 'ABSTAIN')
        self.assertIsNone(result['agreement'])
        self.assertIsNone(result['regret'])
        self.assertEqual(result['comparison']['baseline']['action'], 'DEPLOY')
        self.assertTrue(result['comparison']['baseline']['agreement'])
        self.assertEqual(result['comparison']['validated']['action'], 'ABSTAIN')
        self.assertIsNone(result['comparison']['validated']['constraint_violation'])

    def test_empty_candidate_set_does_not_count_abstention_as_safe_success(self):
        class Policy:
            manifest = {}
            def predict(self, features):
                raise AssertionError('There are no candidates to predict')
        result = module.replay(Policy(), {'case_id': 'empty', 'rows': []})
        self.assertEqual(result['comparison']['baseline']['action'], 'ABSTAIN')
        self.assertIsNone(result['comparison']['baseline']['agreement'])
        self.assertIsNone(result['comparison']['validated']['constraint_violation'])

if __name__ == '__main__':
    unittest.main()
