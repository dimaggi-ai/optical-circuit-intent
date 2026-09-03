"""Bindings from the generic plan to a named plant's documented API.

The intent language compiles to generic operations (``reserve_ports``,
``cross_connect``, ``verify_path``, ``teardown``, ``release_ports``,
``extend_hold``) and hands them back --- DECISIONS.md D2. An adapter turns that
generic plan into the calls one documented controller interface would need,
and hands *those* back too. Nothing in this package opens a session.

One adapter ships:

- ``tapi`` --- the LF ONMI Transport API (TAPI) RESTCONF data tree, profile
  2.1.5 YANG with the TR-547 v1.2 reference implementation agreement.
"""

from . import tapi

__all__ = ["tapi"]
