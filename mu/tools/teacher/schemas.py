"""Parameter schemas of the teacher tools (kept apart from the handlers for size)."""

CREATE_COURSE = {'type': 'object',
 'properties': {'subject': {'type': 'string', 'description': "Subject to teach, e.g. 'Perl', 'Kubernetes'."},
                'target_level': {'type': 'string',
                                 'enum': ['beginner', 'intermediate', 'advanced'],
                                 'default': 'beginner'},
                'learner_summary': {'type': 'string',
                                    'description': 'One-line summary of what the learner said when '
                                                   'starting.'},
                'course_id': {'type': 'string', 'description': 'Optional explicit slug.'}},
 'required': ['subject']}

RECORD_DIAGNOSTIC = {'type': 'object',
 'properties': {'course_id': {'type': 'string'},
                'strengths': {'type': 'array',
                              'items': {'type': 'string'},
                              'description': 'Things the learner already knows well in or near this '
                                             'subject.'},
                'gaps': {'type': 'array',
                         'items': {'type': 'string'},
                         'description': "Specific things they don't know yet that the course must cover."},
                'goals': {'type': 'array',
                          'items': {'type': 'string'},
                          'description': 'What they want to be able to do after the course (be concrete).'},
                'modality': {'type': 'array',
                             'items': {'type': 'string'},
                             'description': 'Preferred learning modalities — pick from: analogy, hands-on, '
                                            'visual, socratic, worked-example, formal-definition, story. '
                                            'Multi-select fine.'},
                'pace': {'type': 'string',
                         'enum': ['slow', 'moderate', 'fast'],
                         'description': 'How fast they want to move through material.'},
                'jargon_tolerance': {'type': 'string',
                                     'enum': ['low', 'medium', 'high'],
                                     'description': 'How comfortable they are with field-specific '
                                                    'terminology and notation up front.'},
                'motivation': {'type': 'string',
                               'description': "Why they're learning this — career, curiosity, specific "
                                              'project, exam, etc.'},
                'background': {'type': 'array',
                               'items': {'type': 'string'},
                               'description': 'Other fields/languages/tools they know well — used as analogy '
                                              "anchors (e.g. 'Python', 'electrical engineering', 'piano')."},
                'personality': {'type': 'string',
                                'description': 'Tone cues — terse vs chatty, likes being pushed back / '
                                               'gentle, humor preferences, anything that should shape '
                                               'voice.'},
                'anchors': {'type': 'array',
                            'items': {'type': 'string'},
                            'description': 'Specific concepts they spontaneously cited as known/comfortable '
                                           '— useful for grounding new ideas.'},
                'notes': {'type': 'string',
                          'description': 'Free-form catch-all for everything else worth remembering.'}}}

UPDATE_LEARNER_PROFILE = {'type': 'object',
 'properties': {'course_id': {'type': 'string'},
                'observation': {'type': 'string',
                                'description': "What you saw that prompted the update (e.g. 'learner "
                                               'answered the cooking-recipe analogy correctly within seconds '
                                               "— analogy-driven works'). Required."},
                'strengths': {'type': 'array', 'items': {'type': 'string'}},
                'gaps': {'type': 'array', 'items': {'type': 'string'}},
                'goals': {'type': 'array', 'items': {'type': 'string'}},
                'modality': {'type': 'array', 'items': {'type': 'string'}},
                'pace': {'type': 'string', 'enum': ['slow', 'moderate', 'fast']},
                'jargon_tolerance': {'type': 'string', 'enum': ['low', 'medium', 'high']},
                'motivation': {'type': 'string'},
                'background': {'type': 'array', 'items': {'type': 'string'}},
                'personality': {'type': 'string'},
                'anchors': {'type': 'array', 'items': {'type': 'string'}},
                'stumbling_blocks': {'type': 'array',
                                     'items': {'type': 'string'},
                                     'description': 'Concrete patterns where the learner is getting stuck '
                                                    "(e.g. 'confuses references and pointers')."},
                'notes': {'type': 'string'}},
 'required': ['observation']}

PROPOSE_CURRICULUM = {'type': 'object',
 'properties': {'course_id': {'type': 'string'},
                'modules': {'type': 'array',
                            'items': {'type': 'object',
                                      'properties': {'module_id': {'type': 'string'},
                                                     'title': {'type': 'string'},
                                                     'goal': {'type': 'string'},
                                                     'order': {'type': 'integer'},
                                                     'mastery_threshold': {'type': 'integer', 'default': 70},
                                                     'lessons': {'type': 'array',
                                                                 'items': {'type': 'object',
                                                                           'properties': {'lesson_id': {'type': 'string'},
                                                                                          'title': {'type': 'string'},
                                                                                          'learning_objectives': {'type': 'array',
                                                                                                                  'items': {'type': 'string'},
                                                                                                                  'description': '1–4 '
                                                                                                                                 'objectives; '
                                                                                                                                 'assignments, '
                                                                                                                                 'checks '
                                                                                                                                 'and '
                                                                                                                                 'reviews '
                                                                                                                                 'map '
                                                                                                                                 'to '
                                                                                                                                 'them.'},
                                                                                          'concept_brief': {'type': 'string'},
                                                                                          'sources': {'type': 'array',
                                                                                                      'items': {'type': 'object',
                                                                                                                'properties': {'title': {'type': 'string'},
                                                                                                                               'url': {'type': 'string'},
                                                                                                                               'kind': {'type': 'string'},
                                                                                                                               'note': {'type': 'string'}},
                                                                                                                'required': ['title',
                                                                                                                             'url']}}},
                                                                           'required': ['lesson_id',
                                                                                        'title',
                                                                                        'learning_objectives']}}},
                                      'required': ['module_id', 'title', 'lessons']}}},
 'required': ['modules']}

ASSIGN_EXERCISE = {'type': 'object',
 'properties': {'lesson_id': {'type': 'string'},
                'assignment_id': {'type': 'string'},
                'kind': {'type': 'string',
                         'enum': ['fix-broken-code',
                                  'implement-from-scratch',
                                  'predict-output',
                                  'multiple-choice',
                                  'fill-blank',
                                  'command-output',
                                  'short-answer',
                                  'explain-trace',
                                  'socratic-dialog']},
                'prompt': {'type': 'string'},
                'rubric': {'type': 'array',
                           'items': {'type': 'object',
                                     'properties': {'criterion': {'type': 'string'},
                                                    'weight': {'type': 'integer'},
                                                    'description': {'type': 'string'}},
                                     'required': ['criterion']}},
                'verification': {'type': 'object',
                                 'properties': {'method': {'type': 'string'},
                                                'verify_cmd': {'type': 'string'},
                                                'expected_markers': {'type': 'array',
                                                                     'items': {'type': 'string'}},
                                                'forbidden_markers': {'type': 'array',
                                                                      'items': {'type': 'string'}},
                                                'expected_answer': {'type': 'string'},
                                                'case_sensitive': {'type': 'boolean'},
                                                'rubric_keywords': {'type': 'array',
                                                                    'items': {'type': 'string'}},
                                                'min_turns': {'type': 'integer'},
                                                'required_concepts': {'type': 'array',
                                                                      'items': {'type': 'string'}},
                                                'timeout_seconds': {'type': 'integer'},
                                                'working_dir': {'type': 'string'},
                                                'use_live_quiz_ui': {'type': 'boolean'}}},
                'artifact_files': {'type': 'array',
                                   'items': {'type': 'object',
                                             'properties': {'path': {'type': 'string'},
                                                            'content': {'type': 'string'}},
                                             'required': ['path']}},
                'pass_threshold': {'type': 'integer', 'default': 70},
                'quiz_questions': {'type': 'array',
                                   'description': 'For multiple-choice / fill-blank assignments: the '
                                                  'per-question payload that drives the live quiz UI.',
                                   'items': {'type': 'object',
                                             'properties': {'qid': {'type': 'string'},
                                                            'prompt': {'type': 'string'},
                                                            'kind': {'type': 'string',
                                                                     'enum': ['multiple_choice',
                                                                              'fill_blank']},
                                                            'options': {'type': 'array',
                                                                        'items': {'type': 'string'}},
                                                            'correct_index': {'type': 'integer'},
                                                            'expected_pattern': {'type': 'string'},
                                                            'case_sensitive': {'type': 'boolean'},
                                                            'explanation': {'type': 'string'}},
                                             'required': ['qid', 'prompt', 'kind']}},
                'review_id': {'type': 'string',
                              'description': 'Set when this exercise is a due spaced review (review id '
                                             'rv<n>).'},
                'objectives': {'type': 'array',
                               'items': {'type': 'string'},
                               'description': "Which of the lesson's learning objectives this exercise tests "
                                              '(default: all).'}},
 'required': ['lesson_id', 'kind', 'prompt']}

SUBMIT_ASSIGNMENT = {'type': 'object',
 'properties': {'assignment_id': {'type': 'string'},
                'submission': {'type': 'object',
                               'description': 'Free-form submission payload. Conventions: {answer: str} for '
                                              'predict-output / short-answer; {answers: {qid: value}} for '
                                              'multi-question quizzes; {notes: str} for code fixes done '
                                              'in-place on the work/ files.'}},
 'required': ['assignment_id']}

RECORD_DIALOG_TURN = {'type': 'object',
 'properties': {'assignment_id': {'type': 'string'},
                'role': {'type': 'string', 'enum': ['agent_question', 'learner_answer']},
                'content': {'type': 'string'},
                'quality_signal': {'type': 'string'}},
 'required': ['assignment_id', 'role', 'content']}

CLOSE_DIALOG = {'type': 'object',
 'properties': {'assignment_id': {'type': 'string'},
                'mastery_pct': {'type': 'integer'},
                'summary': {'type': 'string'},
                'gaps': {'type': 'array', 'items': {'type': 'string'}}},
 'required': ['assignment_id', 'mastery_pct', 'summary']}

WRITE_LECTURE_TRANSCRIPT = {'type': 'object',
 'properties': {'lesson_id': {'type': 'string', 'description': 'The lesson to write the transcript for.'},
                'content': {'type': 'string', 'description': 'Markdown content of the lecture transcript.'}},
 'required': ['lesson_id', 'content']}

REGISTER_EXERCISE_FILE = {'type': 'object',
 'properties': {'lesson_id': {'type': 'string', 'description': 'The lesson to register the exercise for.'},
                'path': {'type': 'string',
                         'description': 'Relative filename within the exercises/ directory (e.g. '
                                        "'example_01.py'). Must not be absolute or contain '..' segments."},
                'content': {'type': 'string', 'description': 'File content to write.'}},
 'required': ['lesson_id', 'path', 'content']}

DECIDE_NEXT = {'type': 'object',
 'properties': {'lesson_id': {'type': 'string'},
                'action': {'type': 'string', 'enum': ['advance', 'remediate']}},
 'required': ['lesson_id', 'action']}

COMPLETE_MODULE = {'type': 'object', 'properties': {'module_id': {'type': 'string'}}, 'required': ['module_id']}
