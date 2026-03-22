"""
init_db.py — One-time database setup script
Run this ONCE before starting the app for the first time.

Usage:
    python init_db.py            # Creates DB + seeds usage_limits
    python init_db.py --reset    # ⚠ Drops everything and rebuilds (dev only)
"""

import sys
from app import app
from models import db, UsageLimit


def seed_usage_limits():
    """
    Insert the plan tier rows into usage_limits.
    These are config values — not changed by users.
    Skips rows that already exist so it's safe to run multiple times.
    """
    plans = [
        {
            'plan':                      'free',
            'max_videos_per_month':      3,
            'max_video_length_secs':     120,           # 2 minutes
            'max_file_size_bytes':       209_715_200,   # 200 MB
            'adversarial_enabled':       False,
            'freq_perturbation_enabled': False,
            'processing_priority':       'low',
        },
        {
            'plan':                      'pro',
            'max_videos_per_month':      50,
            'max_video_length_secs':     1_800,          # 30 minutes
            'max_file_size_bytes':       1_073_741_824,  # 1 GB
            'adversarial_enabled':       True,
            'freq_perturbation_enabled': True,
            'processing_priority':       'medium',
        },
        {
            'plan':                      'business',
            'max_videos_per_month':      -1,             # unlimited
            'max_video_length_secs':     7_200,          # 2 hours
            'max_file_size_bytes':       5_368_709_120,  # 5 GB
            'adversarial_enabled':       True,
            'freq_perturbation_enabled': True,
            'processing_priority':       'high',
        },
    ]

    inserted = 0
    for p in plans:
        # Skip if this plan row already exists
        if UsageLimit.query.get(p['plan']):
            print(f"  [skip] usage_limits: '{p['plan']}' already exists")
            continue
        db.session.add(UsageLimit(**p))
        inserted += 1
        print(f"  [add]  usage_limits: '{p['plan']}'")

    db.session.commit()
    print(f"  → {inserted} plan(s) seeded.")


def create_tables():
    """Create all tables defined in models.py (safe — won't overwrite existing)."""
    db.create_all()
    print("  → All tables created (or already exist).")


def drop_tables():
    """Drop ALL tables. Dev only — never run in production."""
    confirm = input("  ⚠ This will DELETE all data. Type 'yes' to confirm: ")
    if confirm.strip().lower() != 'yes':
        print("  Aborted.")
        sys.exit(0)
    db.drop_all()
    print("  → All tables dropped.")


def main():
    reset = '--reset' in sys.argv

    with app.app_context():
        print("\n── Vigilant Video — Database Setup ──────────────────")

        if reset:
            print("\n[1/3] Dropping all tables...")
            drop_tables()
        
        print("\n[1/2] Creating tables...")
        create_tables()

        print("\n[2/2] Seeding usage_limits...")
        seed_usage_limits()

        print("\n✓ Database ready.")
        print(f"  URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
        print("─────────────────────────────────────────────────────\n")


if __name__ == '__main__':
    main()