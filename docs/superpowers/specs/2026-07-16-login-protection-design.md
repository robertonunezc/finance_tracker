# Login Protection Design

## Goal

Require a signed-in Django user before any application view is accessible, while providing a simple login page for existing Django users and superusers.

## Scope

- Add username/password login and logout routes using Django's built-in auth views.
- Protect the home page and reports views from anonymous access.
- Preserve Django admin behavior.
- Redirect anonymous users to `/accounts/login/` with the original destination in `next`.
- Add a Bootstrap-styled login page that matches the existing base styling.
- Show the current username and a logout control in the authenticated navbar.

## Architecture

The project already has `django.contrib.auth`, session middleware, and auth context processors enabled. The implementation will use Django's built-in session authentication instead of adding custom credentials, JWT handling, or a custom user model.

The route boundary will be explicit: decorate the app views with `login_required`. This keeps future public endpoints possible without maintaining a global middleware allowlist.

## Components

- `finance_tracker.views.home`: protected with `login_required`.
- `reports.views.category_spending`: protected with `login_required`.
- `finance_tracker.urls`: includes Django auth URLs under `/accounts/`.
- `templates/registration/login.html`: renders the login form expected by `LoginView`.
- `templates/base.html`: displays authenticated user state and logout form.
- `finance_tracker.settings`: defines `LOGIN_URL`, `LOGIN_REDIRECT_URL`, and `LOGOUT_REDIRECT_URL`.

## Data Flow

Anonymous requests to protected app views receive a `302` redirect to `/accounts/login/?next=<path>`. Valid username/password submissions authenticate through Django's existing auth backend, set the session cookie, and redirect to `next` when present or home otherwise. Logout uses Django's logout view and redirects back to login.

## Error Handling

Invalid login submissions re-render the login template with Django's form errors. Protected views do not perform custom error handling; Django's `login_required` handles redirects.

## Testing

Tests will verify anonymous redirects for home and reports, authenticated access to protected pages, login redirect behavior, invalid login errors, and logout redirect behavior.
