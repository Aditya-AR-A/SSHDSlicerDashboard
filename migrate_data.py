import sys
import json
from pathlib import Path
sys.path.insert(0, 'src')
from slicing_dashboard.db import DatabaseManager

def migrate():
    dm = DatabaseManager()
    if not dm.is_connected():
        print("Not connected to DB")
        return
        
    # Migrate user mappings
    mapping_file = Path('config/user_mapping.json')
    if mapping_file.exists():
        with open(mapping_file) as f:
            mapping_dict = json.load(f)
        
        mappings_list = []
        for k, v in mapping_dict.items():
            mappings_list.append({
                'id': k,
                'mapped_user': v,
                'mapping_type': 'Existing'
            })
        
        dm.save_user_mappings(mappings_list)
        print(f"Migrated {len(mappings_list)} user mappings.")
        
    # Migrate settlement periods
    periods = [
        {'_id': '2026-07-01_2026-08-07', 'start_date': '2026-07-01', 'end_date': '2026-08-07', 'settled': True},
        {'_id': '2026-08-08_2026-08-31', 'start_date': '2026-08-08', 'end_date': '2026-08-31', 'settled': True},
        {'_id': '2026-09-01_2026-09-30', 'start_date': '2026-09-01', 'end_date': '2026-09-30', 'settled': True},
    ]
    dm.save_settlement_periods(periods)
    print(f"Migrated {len(periods)} settlement periods.")

if __name__ == '__main__':
    migrate()
