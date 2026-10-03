
from ..rust_weight_engine import RustWeightEngine


GUMROAD_PRODUCT_ID = "SNwmonGFn_waEKPkW369ZA=="

MAX_DEVICE_ACTIVATIONS = 1000


class LicenseGateway:
    """Stateless service -- all methods are classmethods, no instance state."""


    @classmethod
    def check_activation_status(cls, license_key: str) -> dict:
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
        success, message, token = cls._verify_license(license_key)
        from ..preferences.preferences_service import PreferencesService
        if success:
            PreferencesService.set_license_activation(license_key, token, message)
        else:
            PreferencesService.set_license_status_message(message)
        return success, message

    @classmethod
    def _verify_license(cls, license_key: str) -> tuple[bool, str, str]:
        rust = RustWeightEngine("license_activation")
        return rust.call("rust_verify_gumroad_license", license_key, GUMROAD_PRODUCT_ID)


    @classmethod
    def check_cached_activation(cls, license_key: str, activation_token: str) -> bool:
        if not license_key or not activation_token:
            return False
        rust = RustWeightEngine("license_check")
        return rust.call("rust_check_cached_activation", license_key, activation_token)


    @classmethod
    def is_pro(cls) -> bool:
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
