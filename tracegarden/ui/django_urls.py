"""
tracegarden.ui.django_urls
~~~~~~~~~~~~~~~~~~~~~~~~~~
Django URL patterns for the TraceGarden UI.

Usage in urls.py::

    from django.urls import path, include

    urlpatterns = [
        ...
        path("__tracegarden/", include("tracegarden.ui.django_urls")),
    ]
"""
from tracegarden.ui.routes import mount_django_urls

# use_include=True: routes have no prefix (handled by path() in urls.py),
# but ui_prefix from TRACEGARDEN settings is still used for asset URLs in templates.
urlpatterns = mount_django_urls(use_include=True)
