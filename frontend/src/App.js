import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import {
    AdminRoute,
    ProtectedRoute,
} from "@/components/auth/ProtectedRoute";
import { AppShell } from "@/components/layout/AppShell";

// Public, critical-path pages stay eager so first paint is instant.
import Landing from "@/pages/Landing";
import Login from "@/pages/Login";

// Everything else is code-split into its own chunk and loaded on demand. This keeps
// heavy deps (recharts, framer-motion, admin views) out of the initial bundle, so the
// app shell paints fast instead of waiting on the whole application to download.
const Signup = lazy(() => import("@/pages/Signup"));
const VerifyOtp = lazy(() => import("@/pages/VerifyOtp"));
const ForgotPassword = lazy(() => import("@/pages/ForgotPassword"));

const Dashboard = lazy(() => import("@/pages/Dashboard"));
const Projects = lazy(() => import("@/pages/Projects"));
const ProjectDetail = lazy(() => import("@/pages/ProjectDetail"));
const AnalyzeWizard = lazy(() => import("@/pages/AnalyzeWizard"));
const AnalysisReport = lazy(() => import("@/pages/AnalysisReport"));
const RfiKanban = lazy(() => import("@/pages/RfiKanban"));
const Outputs = lazy(() => import("@/pages/Outputs"));
const RiskDashboard = lazy(() => import("@/pages/RiskDashboard"));

const SettingsProfile = lazy(() =>
    import("@/pages/Settings").then((m) => ({ default: m.SettingsProfile })),
);
const SettingsSecurity = lazy(() =>
    import("@/pages/Settings").then((m) => ({ default: m.SettingsSecurity })),
);

const AdminUsers = lazy(() =>
    import("@/pages/admin/AdminUsers").then((m) => ({ default: m.AdminUsers })),
);
const AdminPermissionsList = lazy(() =>
    import("@/pages/admin/AdminPermissions").then((m) => ({ default: m.AdminPermissionsList })),
);
const AdminPermissionEditor = lazy(() =>
    import("@/pages/admin/AdminPermissions").then((m) => ({ default: m.AdminPermissionEditor })),
);
const AdminAuditLog = lazy(() =>
    import("@/pages/admin/AdminAuditLog").then((m) => ({ default: m.AdminAuditLog })),
);
const AdminAnalytics = lazy(() =>
    import("@/pages/admin/AdminAnalytics").then((m) => ({ default: m.AdminAnalytics })),
);

function PageFallback() {
    return (
        <div className="flex min-h-screen items-center justify-center bg-background">
            <div className="font-mono text-xs uppercase tracking-wider text-ink-muted">
                Loading…
            </div>
        </div>
    );
}

function AppRoute({ children }) {
    return (
        <ProtectedRoute>
            <AppShell>{children}</AppShell>
        </ProtectedRoute>
    );
}

function AdminAppRoute({ children }) {
    return (
        <ProtectedRoute>
            <AdminRoute>
                <AppShell>{children}</AppShell>
            </AdminRoute>
        </ProtectedRoute>
    );
}

function RootGate() {
    const { user, loading } = useAuth();

    if (loading) return null;

    return user ? (
        <Navigate to="/dashboard" replace />
    ) : (
        <Landing />
    );
}

export default function App() {
    return (
        <AuthProvider>
            <Toaster
                position="top-right"
                toastOptions={{
                    className:
                        "!rounded-none !border !border-ink-line !bg-white !text-navy !shadow-lg !font-body",
                }}
            />

            <BrowserRouter>
                <Suspense fallback={<PageFallback />}>
                <Routes>
                    {/* PUBLIC ROUTES */}
                    <Route path="/" element={<RootGate />} />
                    <Route path="/login" element={<Login />} />
                    <Route path="/signup" element={<Signup />} />
                    <Route path="/verify-otp" element={<VerifyOtp />} />
                    <Route
                        path="/forgot-password"
                        element={<ForgotPassword />}
                    />

                    {/* APP ROUTES */}
                    <Route
                        path="/dashboard"
                        element={
                            <AppRoute>
                                <Dashboard />
                            </AppRoute>
                        }
                    />

                    <Route
                        path="/projects"
                        element={
                            <AppRoute>
                                <Projects />
                            </AppRoute>
                        }
                    />

                    <Route
                        path="/projects/:id"
                        element={
                            <AppRoute>
                                <ProjectDetail />
                            </AppRoute>
                        }
                    />

                    <Route
                        path="/projects/:id/analyze"
                        element={
                            <AppRoute>
                                <AnalyzeWizard />
                            </AppRoute>
                        }
                    />

                    <Route
                        path="/analyze"
                        element={
                            <AppRoute>
                                <AnalyzeWizard />
                            </AppRoute>
                        }
                    />

                    <Route
                        path="/analyses/:id"
                        element={
                            <AppRoute>
                                <AnalysisReport />
                            </AppRoute>
                        }
                    />

                    <Route
                        path="/rfi-tracker"
                        element={
                            <AppRoute>
                                <RfiKanban />
                            </AppRoute>
                        }
                    />

                    <Route
                        path="/outputs"
                        element={
                            <AppRoute>
                                <Outputs />
                            </AppRoute>
                        }
                    />

                    <Route
                        path="/risk-dashboard"
                        element={
                            <AppRoute>
                                <RiskDashboard />
                            </AppRoute>
                        }
                    />

                    {/* SETTINGS */}
                    <Route
                        path="/settings"
                        element={
                            <Navigate
                                to="/settings/profile"
                                replace
                            />
                        }
                    />

                    <Route
                        path="/settings/profile"
                        element={
                            <AppRoute>
                                <SettingsProfile />
                            </AppRoute>
                        }
                    />

                    <Route
                        path="/settings/security"
                        element={
                            <AppRoute>
                                <SettingsSecurity />
                            </AppRoute>
                        }
                    />

                    {/* SUPER ADMIN */}
                    <Route
                        path="/admin"
                        element={
                            <Navigate
                                to="/admin/users"
                                replace
                            />
                        }
                    />

                    <Route
                        path="/admin/users"
                        element={
                            <AdminAppRoute>
                                <AdminUsers />
                            </AdminAppRoute>
                        }
                    />

                    <Route
                        path="/admin/permissions"
                        element={
                            <AdminAppRoute>
                                <AdminPermissionsList />
                            </AdminAppRoute>
                        }
                    />

                    <Route
                        path="/admin/permissions/:uid"
                        element={
                            <AdminAppRoute>
                                <AdminPermissionEditor />
                            </AdminAppRoute>
                        }
                    />

                    <Route
                        path="/admin/audit-log"
                        element={
                            <AdminAppRoute>
                                <AdminAuditLog />
                            </AdminAppRoute>
                        }
                    />

                    <Route
                        path="/admin/analytics"
                        element={
                            <AdminAppRoute>
                                <AdminAnalytics />
                            </AdminAppRoute>
                        }
                    />

                    {/* FALLBACK */}
                    <Route
                        path="*"
                        element={<Navigate to="/" replace />}
                    />
                </Routes>
                </Suspense>
            </BrowserRouter>
        </AuthProvider>
    );
}