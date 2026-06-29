import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

/**
 * Loads the current user's feature_permissions document.
 * Super admin returns a wildcard bundle directly from the API.
 *
 * Usage:
 *   const { perms, loading, can, modeAllowed } = usePermissions();
 *   if (can("canRunEstimation")) { ... }
 *   if (modeAllowed("DETAILER_MODE_1")) { ... }
 */
export function usePermissions() {
    const { user } = useAuth();
    const [perms, setPerms] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            if (!user) {
                setPerms(null);
                setLoading(false);
                return;
            }
            try {
                const { data } = await api.get("/api/admin/me/permissions");
                if (!cancelled) setPerms(data);
            } catch {
                if (!cancelled) setPerms(null);
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [user?.id]);

    const isSuperAdmin = user?.role === "super_admin";

    const can = (flag) => {
        if (isSuperAdmin) return true;
        if (!perms) return false;
        return !!perms[flag];
    };

    const modeAllowed = (modeId) => {
        if (isSuperAdmin) return true;
        const list = perms?.allowedModes || [];
        return list.includes("*") || list.includes(modeId);
    };

    const exportAllowed = (fmt) => {
        if (isSuperAdmin) return true;
        const list = perms?.allowedExports || [];
        return list.includes("*") || list.includes(fmt);
    };

    const countryAllowed = (code) => {
        if (isSuperAdmin) return true;
        const list = perms?.estimationCountries || [];
        return list.includes(code);
    };

    return { perms, loading, isSuperAdmin, can, modeAllowed, exportAllowed, countryAllowed };
}
