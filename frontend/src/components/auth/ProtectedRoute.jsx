import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";

export function ProtectedRoute({ children }) {
    const { user, loading } = useAuth();
    const loc = useLocation();

    if (loading) {
        return (
            <div
                data-testid="auth-loading"
                className="flex h-screen items-center justify-center bg-navy text-white"
            >
                <div className="font-mono text-xs uppercase tracking-[0.3em] text-gold">
                    STRUCTMIND · LOADING
                </div>
            </div>
        );
    }
    if (!user) {
        return (
            <Navigate to="/login" state={{ from: loc.pathname }} replace />
        );
    }
    return children;
}

export function AdminRoute({ children }) {
    const { user, loading } = useAuth();
    if (loading) return null;
    if (!user || user.role !== "super_admin") {
        return <Navigate to="/dashboard" replace />;
    }
    return children;
}
