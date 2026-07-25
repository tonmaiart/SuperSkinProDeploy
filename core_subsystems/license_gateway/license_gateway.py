"""LicenseGateway -- unified portal for Pro-activation state and feature gating.

Merges the responsibilities of the former license/license_gateway.py (Rust FFI
dispatch for Gumroad verification and HMAC token checking) and
license/license_service.py (persistence and feature-gating logic).

All methods are classmethods; no instance state is maintained.
Persistence routes through PreferencesService; the HTTPS call and HMAC
signing/verification happen inside the compiled Rust binary.

No bpy import -- operates on plain strings and booleans.
"""

from ..rust_weight_engine import RustWeightEngine

# PreferencesService is deliberately NOT hoisted here. core_subsystems/__init__.py
# reloads license_gateway before preferences (see that file's module list), so
# a module-level import here would bind to the pre-reload PreferencesService
# class -- a different object than the one load()/save_to_user_file() actually
# operate on, which silently defeats the `_loading` reentrancy guard. Every
# call site below imports it fresh instead. See docs/bug-history for the
# write-up this class of bug was diagnosed from.

# This product's Gumroad API requires product_id (not product_permalink).
GUMROAD_PRODUCT_ID = "SNwmonGFn_waEKPkW369ZA=="

# Gumroad has no built-in per-device activation cap of its own -- it only
# ever returns a raw "how many times has this key been verified with
# increment_uses_count=true" counter (see rust_logic/src/license_logic.rs's
# GumroadResponse.uses). This is our own business rule, enforced entirely in
# Python: check_activation_status() below is what the UI calls to decide
# whether an activation needs user confirmation (device 2..MAX) or should be
# denied outright (device MAX+1). Purely a UX-level deterrent, not
# cryptographic enforcement -- someone willing to script around the UI could
# still call activate() directly. Tune freely; no Rust rebuild required.
MAX_DEVICE_ACTIVATIONS = 1000


class LicenseGateway:
    """Stateless service -- all methods are classmethods, no instance state."""

    # =========================================================================
    #  Activation (network-dependent)
    # =========================================================================

    @classmethod
    def check_activation_status(cls, license_key: str) -> dict:
        """Dry-run check of *license_key*'s current Gumroad device-use count.

        Does not call Gumroad's incrementing endpoint, so this can be called
        freely by the UI to decide what to show the user *before* actually
        committing an activation via :meth:`activate`.

        Returns:
            ``{"valid": bool, "message": str, "uses": int, "max_uses": int,
            "at_limit": bool}``. ``uses`` is the number of devices already
            activated *before* this one would be counted. ``at_limit`` is
            True once ``uses >= MAX_DEVICE_ACTIVATIONS`` -- the caller should
            deny activation outright rather than offer a confirm prompt.
        """
        rust = RustWeightEngine("license_activation")
        valid, message, uses = rust.call(
            "rust_peek_gumroad_uses", license_key, GUMROAD_PRODUCT_ID
        )
        return {
            "valid": valid,
            "message": message,
            "uses": uses,
            "max_uses": MAX_DEVICE_ACTIVATIONS,
            "at_limit": uses >= MAX_DEVICE_ACTIVATIONS,
        }

    @classmethod
    def activate(cls, license_key: str) -> tuple[bool, str]:
        """Verify *license_key* against Gumroad (requires internet) and persist.

        Always counts as a use on Gumroad's side. Callers should call
        :meth:`check_activation_status` first and only reach this method
        once the caller (and, for devices 2..MAX, the user via an explicit
        confirmation prompt) has already decided the activation should
        proceed -- this method itself does not ask for confirmation.

        Only persists *license_key* to disk on success. A rejected key is
        never written to ``user.json`` -- the caller still gets it back in
        *message* to report to the user for this session, but it must not
        survive an F3 Reload Scripts (or a Blender restart) and reappear
        pre-filled in the activation field.

        Returns:
            ``(success, message)`` for the calling operator to report.
        """
        success, message, token = cls._verify_license(license_key)
        from ..preferences.preferences_service import PreferencesService
        if success:
            PreferencesService.set_license_activation(license_key, token, message)
        else:
            PreferencesService.set_license_status_message(message)
        return success, message

    @classmethod
    def _verify_license(cls, license_key: str) -> tuple[bool, str, str]:
        """Call Gumroad's License Verify API.

        Returns ``(success, message, activation_token)``. *activation_token* is
        only non-empty when *success* is True.
        """
        rust = RustWeightEngine("license_activation")
        return rust.call("rust_verify_gumroad_license", license_key, GUMROAD_PRODUCT_ID)

    # =========================================================================
    #  Offline re-check
    # =========================================================================

    @classmethod
    def check_cached_activation(cls, license_key: str, activation_token: str) -> bool:
        """Offline re-check of a previously issued *activation_token*. No network."""
        if not license_key or not activation_token:
            return False
        rust = RustWeightEngine("license_check")
        return rust.call("rust_check_cached_activation", license_key, activation_token)

    # =========================================================================
    #  State queries
    # =========================================================================

    @classmethod
    def is_pro(cls) -> bool:
        """True if the cached activation token is currently valid.

        Always re-derives the signature via Rust rather than trusting a stored
        boolean -- see SSPrefLicense docstring for why.
        """
        from ..preferences.preferences_service import PreferencesService
        key = PreferencesService.get_license_key()
        token = PreferencesService.get_activation_token()
        return cls.check_cached_activation(key, token)

    @classmethod
    def get_license_key(cls) -> str:
        from ..preferences.preferences_service import PreferencesService
        return PreferencesService.get_license_key()

    @classmethod
    def get_activation_token(cls) -> str:
        from ..preferences.preferences_service import PreferencesService
        return PreferencesService.get_activation_token()
