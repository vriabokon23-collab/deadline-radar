"""Navigate the public Streamlit pages in frontend regression tests."""
PAGE_PATHS = {
    '📊 Dashboard': 'overview', '📈 Workload Heatmap': 'workload',
    '📄 Upload & Review': 'import_handbooks', '✏️ Manage Assessments': 'assessments',
    '⏱️ Progress Tracking': 'assessments', '🗓️ Plan My Week': 'planner',
    'Overview': 'overview', 'Assessments': 'assessments', 'Planner': 'planner',
    'Workload': 'workload', 'Import': 'import_handbooks', 'Settings': 'settings',
}


def navigate(at, page):
    at.switch_page(f'app_pages/{PAGE_PATHS[page]}.py').run()
    if page in ('⏱️ Progress Tracking', '✏️ Manage Assessments'):
        view = 'Progress' if page == '⏱️ Progress Tracking' else 'Edit'
        at.segmented_control(key='assessment_view').set_value(view).run()
    return at
