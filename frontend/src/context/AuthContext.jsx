import {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useMemo,
    useState,
} from "react";
import { api } from "@/lib/api";
import { tokenStore } from "@/lib/auth";

const AuthContext = createContext(null);

const IMP_KEY = "structmind.impersonation";
const IMP_TOKEN_KEY = "structmind.impersonation_token";
const IMP_PRIOR_KEY = "structmind.impersonation_prior";

export function AuthProvider({ children }) {
    const [user, setUser] = useState(() => tokenStore.getUser());
    const [loading, setLoading] = useState(true);
    const [impersonation, setImpersonation] = useState(() => {
        try {
            const raw = sessionStorage.getItem(IMP_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch {
            return null;
        }
    });

    useEffect(() => {
        const bootstrap = async () => {
            const token = tokenStore.getAccess();
            if (!token) {
                setLoading(false);
                return;
            }
            try {
                const { data } = await api.get("/api/auth/me");
                setUser(data);
                tokenStore.setUser(data);
            } catch {
                tokenStore.clear();
                setUser(null);
            } finally {
                setLoading(false);
            }
        };
        bootstrap();
    }, []);

    const login = useCallback((tokens, u) => {
        tokenStore.setTokens(tokens.access_token, tokens.refresh_token);
        tokenStore.setUser(u);
        setUser(u);
    }, []);

    const logout = useCallback(async () => {
        try {
            await api.post("/api/auth/logout");
        } catch {}
        tokenStore.clear();
        sessionStorage.removeItem(IMP_KEY);
        sessionStorage.removeItem(IMP_TOKEN_KEY);
        sessionStorage.removeItem(IMP_PRIOR_KEY);
        setImpersonation(null);
        setUser(null);
    }, []);

    const refreshUser = useCallback(async () => {
        try {
            const { data } = await api.get("/api/auth/me");
            setUser(data);
            tokenStore.setUser(data);
        } catch {}
    }, []);

    const startImpersonation = useCallback(async (targetUserId) => {
        const { data } = await api.post("/api/admin/impersonate", { user_id: targetUserId });
        // Preserve current admin token+user so we can return seamlessly
        const priorAccess = tokenStore.getAccess();
        const priorRefresh = tokenStore.getRefresh();
        const priorUser = tokenStore.getUser();
        sessionStorage.setItem(
            IMP_PRIOR_KEY,
            JSON.stringify({ access: priorAccess, refresh: priorRefresh, user: priorUser }),
        );
        // Swap to impersonated session — no refresh token issued.
        tokenStore.setTokens(data.access_token, priorRefresh || data.access_token);
        const impUser = {
            ...data.target_user,
            is_verified: true,
            is_active: true,
            subscription_tier: "free",
            usage_this_month: { analyses: 0, files_processed: 0, total_file_size_mb: 0 },
            _impersonated: true,
        };
        tokenStore.setUser(impUser);
        setUser(impUser);
        const impMeta = {
            target_user_id: data.target_user.id,
            email: data.target_user.email,
            role: data.target_user.role,
            expiresInMinutes: data.expires_in_minutes,
            startedAt: Date.now(),
        };
        sessionStorage.setItem(IMP_KEY, JSON.stringify(impMeta));
        sessionStorage.setItem(IMP_TOKEN_KEY, data.access_token);
        setImpersonation(impMeta);
    }, []);

    const stopImpersonation = useCallback(() => {
        try {
            const raw = sessionStorage.getItem(IMP_PRIOR_KEY);
            if (raw) {
                const prior = JSON.parse(raw);
                tokenStore.setTokens(prior.access, prior.refresh);
                tokenStore.setUser(prior.user);
                setUser(prior.user);
            }
        } catch {}
        sessionStorage.removeItem(IMP_KEY);
        sessionStorage.removeItem(IMP_TOKEN_KEY);
        sessionStorage.removeItem(IMP_PRIOR_KEY);
        setImpersonation(null);
    }, []);

    const value = useMemo(
        () => ({
            user,
            loading,
            login,
            logout,
            refreshUser,
            setUser,
            impersonation,
            startImpersonation,
            stopImpersonation,
        }),
        [user, loading, impersonation, login, logout, refreshUser, startImpersonation, stopImpersonation],
    );
    return (
        <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
    );
}

export function useAuth() {
    const ctx = useContext(AuthContext);
    if (!ctx) throw new Error("useAuth must be used within AuthProvider");
    return ctx;
}
