# Finance Tracker AI Agent Instructions

## Project Overview
Django 6.0 finance tracker with Telegram bot integration for receipt processing. Mexican market receipts are uploaded via Telegram, stored in S3, and processed using GPT-4 Vision OCR for expense tracking.

## Architecture

### Core Components
- **Django Apps**: Modular Django structure with `receipt`, `extract_info`, `handle_files`, `telegram_bot`, `user`, `auth`
- **Telegram Bot** ([telegram_bot/main.py](telegram_bot/main.py)): Primary user interface - receives photos, manages auth, orchestrates workflows
- **Receipt Processing Pipeline**: Upload → Pending → Processing → Completed/Failed with user notifications at each stage
- **Storage**: AWS S3 for images (`handle_files/services/upload.py`), PostgreSQL for structured data, Redis for caching

### Service Layer Pattern
Use factory pattern for services:
```python
# Upload service
upload_service = UploadServiceFactory.create()  # Returns AwsUploadService

# Services use abstract base classes (ABC) - see handle_files/services/upload.py
```

Note: `RepositoryFactory` is referenced in [telegram_bot/main.py](telegram_bot/main.py#L33) but missing implementation - use Django ORM directly via `receipt.services` module.

### Data Models
- **Receipt** ([receipt/models.py](receipt/models.py)): UUIDs for IDs, status tracking (pending/processing/completed/failed), user_id as string
- **ReceiptItem**: ForeignKey to Receipt with `related_name='items'`
- **Category**: TextChoices enum with 30+ categories optimized for Mexican grocery receipts (groceries, beverages, tortillas, etc.)
- **Dataclasses** ([receipt/dataclasses.py](receipt/dataclasses.py)): Use for data transfer between layers, not Django models directly

## Development Workflows

### Local Development Setup
```bash
# Start dependencies (Postgres + Redis)
docker-compose -f docker-compose.dev.yml up -d

# Environment variables required (create .env):
# - POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_PORT
# - AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_BUCKET_NAME
# - OPENAI_API_KEY (for GPT-4 Vision)
# - TELEGRAM_BOT_TOKEN
# - ALLOWED_USERS (comma-separated user IDs)
# - JWT_SECRET
# - REDIS_PASSWORD

# Database migrations
make migrate

# Run Telegram bot (standalone, not Django runserver)
python3 ./telegram_bot/main.py

# Django admin
make runserver
python3 manage.py createsuperuser
```

### Key Commands (Makefile)
- `make install`: pip install requirements
- `make migrate`: makemigrations + migrate
- `make test`: Django test runner (no tests implemented yet)
- `make format`: black + isort (code formatting)
- `make clean`: Remove __pycache__ files

## Critical Patterns & Conventions

### Receipt Processing Workflow
3-phase workflow with user notifications ([telegram_bot/main.py](telegram_bot/main.py#L106-L283)):
1. **Upload & Pending**: Upload to S3, create DB record, notify user immediately
2. **Processing**: Update status, call GPT-4 Vision for OCR extraction
3. **Completion**: Parse JSON, create ReceiptItems, update total/status, send summary

**GPT-4 Vision Prompt** ([extract_info/services.py](extract_info/services.py#L27)): Requests structured JSON with items array, handles Spanish receipts, uses "other" for unclear categories (no hallucination).

### JSON Response Handling
Multi-strategy parsing to handle GPT markdown formatting ([telegram_bot/main.py](telegram_bot/main.py#L174-L196)):
```python
# 1. Strip markdown code blocks
cleaned = response.replace('```json', '').replace('```', '').strip()
# 2. Try direct parse
# 3. Regex extraction if needed
```

### Authentication
- **Telegram Bot**: User ID whitelist in `ALLOWED_USERS`, permanent ban list in `banned.txt` with async lock
- **JWT Tokens**: Generated via `/generate_token` command, 7-day expiry, HS256 algorithm
- No Django user authentication implemented yet (user/models.py is empty)

### File Upload Structure
S3 path pattern: `uploads/tickets/{filename}` ([upload.py](handle_files/services/upload.py#L92))
URLs: `https://{bucket}.s3.{region}.amazonaws.com/uploads/tickets/{filename}`

## Integration Points

### External Services
- **OpenAI GPT-4o-mini**: Vision API for receipt OCR ([extract_info/services.py](extract_info/services.py))
- **AWS S3**: boto3 client with env-based credentials
- **Telegram Bot API**: python-telegram-bot library (async handlers)
- **PostgreSQL**: psycopg2 + psycopg[binary] for Django ORM
- **Redis**: django-redis for caching (configured but minimal usage)

### Django Settings Specifics
- Uses `python-dotenv` for environment loading ([settings.py](finance_tracker/settings.py#L88))
- PostgreSQL configured as default DB
- No Redis cache config in settings yet despite dependencies
- Django Jazzmin for admin UI styling

## Project-Specific Notes

### Known Issues/Incomplete Areas
1. **Missing Repository Layer**: [telegram_bot/main.py](telegram_bot/main.py#L7) imports `RepositoryFactory` and `ServiceType` from non-existent `services.store_data.store_data` - use `receipt.services` functions instead
2. **test_db_conn.py**: References wrong settings module `water_delivery.settings` (should be `finance_tracker.settings`)
3. **No Tests**: All apps have empty `tests.py` files
4. **entrypoint.sh**: References `water_delivery.wsgi` (copy-paste from different project)

### Language & Locale
- Receipts are Spanish (Mexican market)
- Currency: Mexican Pesos
- Date handling: Timezone set to UTC ([settings.py](finance_tracker/settings.py#L125))

### Logging
- Python logging configured with INFO level
- Structured logging library installed (`python-json-logger`) but not configured
- Manual `logger.info/error` calls throughout code

## File Organization
```
finance_tracker/          # Django project settings
receipt/                  # Core domain: models, services, dataclasses
extract_info/             # GPT-4 Vision OCR service
handle_files/services/    # S3 upload abstraction with factory pattern
telegram_bot/             # Standalone bot (not Django-managed), main entry point
user/                     # Empty - no custom user model yet
auth/                     # Empty - using Telegram auth only
```

## When Modifying Code
1. **Receipt Status Changes**: Always notify user via Telegram, update DB atomically
2. **Adding Categories**: Extend `Category` TextChoices in [receipt/models.py](receipt/models.py#L14)
3. **New Services**: Follow ABC pattern with factory (see [upload.py](handle_files/services/upload.py))
4. **Database Changes**: Use `make migrate`, Docker Postgres must be running
5. **Environment Variables**: Update `.env` and document here
6. **OCR Improvements**: Modify prompt in [extract_info/services.py](extract_info/services.py#L27), test with Spanish receipts
