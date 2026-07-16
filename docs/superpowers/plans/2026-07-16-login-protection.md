# Login Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Django login page and require authentication for all application views.

**Architecture:** Use Django's built-in auth URLs, session authentication, and `login_required` decorators. Keep protection explicit on the app views so admin and future public endpoints are not accidentally blocked by global middleware.

**Tech Stack:** Django 6.0, Django test client, Bootstrap templates.

## Global Constraints

- Use existing Django users and superusers; do not add a custom user model.
- Anonymous app-view requests redirect to `/accounts/login/` with `next`.
- Admin stays on Django's existing admin authentication path.
- Write failing tests before production code changes.

---

### Task 1: Auth Behavior Tests

**Files:**
- Modify: `reports/tests.py`

**Interfaces:**
- Consumes: existing `home` and `reports:category-spending` URL names.
- Produces: test expectations for login route, protected routes, login redirects, and logout redirects.

- [ ] **Step 1: Write failing tests**

Add tests that create a Django user, assert anonymous redirects to login for home and report pages, assert authenticated users can render both pages, assert a valid login follows `next`, assert invalid login displays an error, and assert logout redirects to login.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test reports.tests.CategorySpendingViewTests -v 2`

Expected: failures showing current anonymous access returns `200` instead of redirect and `/accounts/login/` is not configured.

### Task 2: Login Routes, Settings, and Templates

**Files:**
- Modify: `finance_tracker/urls.py`
- Modify: `finance_tracker/settings.py`
- Modify: `templates/base.html`
- Create: `templates/registration/login.html`
- Modify: `static/css/base.css`

**Interfaces:**
- Consumes: Django's `django.contrib.auth.urls`.
- Produces: `/accounts/login/`, `/accounts/logout/`, login redirects, authenticated navbar state, and login page UI.

- [ ] **Step 1: Implement minimal auth routing and template support**

Include Django auth URLs, define login/logout redirect settings, add a login template using the built-in `AuthenticationForm`, show username/logout in the navbar, and add small CSS rules for the auth page.

- [ ] **Step 2: Run tests to verify route/template behavior**

Run: `python manage.py test reports.tests.CategorySpendingViewTests -v 2`

Expected: protected-view tests still fail until decorators are added, while login URL tests progress to template/form assertions.

### Task 3: Protect App Views

**Files:**
- Modify: `finance_tracker/views.py`
- Modify: `reports/views.py`

**Interfaces:**
- Consumes: Django's `login_required` decorator.
- Produces: protected home and report views.

- [ ] **Step 1: Add view decorators**

Decorate `home` and `category_spending` with `login_required`.

- [ ] **Step 2: Run focused auth tests**

Run: `python manage.py test reports.tests.CategorySpendingViewTests -v 2`

Expected: all auth behavior tests pass.

- [ ] **Step 3: Run full test suite**

Run: `python manage.py test -v 2`

Expected: test suite exits with status `0`.
