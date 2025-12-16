import os, traceback
os.environ.setdefault('DJANGO_SETTINGS_MODULE','water_delivery.settings')
try:
    from django.db import connections
    conn = connections['default']
    conn.ensure_connection()
    print("DB connection OK")
except Exception:
    traceback.print_exc()