import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class DatabaseEnvironmentTests(unittest.TestCase):
    def test_django_database_env_overrides_host_postgres_port(self):
        project_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env.update(
            {
                "DJANGO_DB_HOST": "postgres",
                "DJANGO_DB_PORT": "5432",
                "POSTGRES_HOST": "postgres",
                "POSTGRES_PORT": "5440",
                "PYTHONPATH": str(project_root),
            }
        )
        script = (
            "import json; "
            "from finance_tracker.settings import DATABASES; "
            "db = DATABASES['default']; "
            "print(json.dumps({'HOST': db['HOST'], 'PORT': db['PORT']}))"
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            env=env,
            text=True,
        )

        self.assertEqual(
            json.loads(result.stdout),
            {"HOST": "postgres", "PORT": "5432"},
        )
