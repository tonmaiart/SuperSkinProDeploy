"""Shared Operator.execute() body for Unified Registry-routed operators.

Routes through ``UnifiedRegistry.execute`` by domain_id + action.
"""

import bpy


def run_domain_via_unified(context, domain_id: str, action: str):
    """Shared execute body for Unified Registry-routed operators.

    Routes through ``UnifiedRegistry.execute`` by domain_id + action.
    """
    from ..registry.register_api import UnifiedRegistry
    from ...core.facade import CoreFacade
    context.scene.superskin_internal_transaction = True
    try:
        facade = CoreFacade(context)
        result = UnifiedRegistry.execute(domain_id, action, context, facade)
        if result.get("status") == "CANCELLED":
            return {'CANCELLED'}
        return {'FINISHED'}
    except ValueError:
        return {'CANCELLED'}
    finally:
        context.scene.superskin_internal_transaction = False
