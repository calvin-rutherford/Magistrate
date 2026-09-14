from app.db import get_profile, update_profile


def test_background_customization_persistence():
    prof = get_profile('default_user')
    assert prof['user_id'] == 'default_user'
    up = update_profile('default_user', bio='Voice-first operator')
