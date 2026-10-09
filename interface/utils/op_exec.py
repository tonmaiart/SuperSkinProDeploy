
import bpy


def run_domain_via_unified(context, domain_id: str, action: str, op=None):
    from ..registry.register_api import UnifiedRegistry
    from ...core.facade import CoreFacade
    context.scene.superskin_internal_transaction = True
    try:
        facade = CoreFacade(context)
        result = UnifiedRegistry.execute(domain_id, action, context, facade)
        cancelled = result.get("status") == "CANCELLED"
        message = result.get("message")
        if op is not None and message:
            op.report({'WARNING'} if cancelled else {'INFO'}, message)
        return {'CANCELLED'} if cancelled else {'FINISHED'}
    except ValueError as e:
        if op is not None:
            op.report({'WARNING'}, str(e))
        return {'CANCELLED'}
    finally:
        context.scene.superskin_internal_transaction = False
