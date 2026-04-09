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

urlpatterns = mount_django_urls()
