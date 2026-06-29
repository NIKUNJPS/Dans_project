import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import {
    AdminRoute,
    ProtectedRoute,
} from "@/components/auth/ProtectedRoute";
import { AppShell } from "@/components/layout/AppShell";

import Landing from "@/pages/Landing";
import Login from "@/pages/Login";
import Signup from "@/pages/Signup";
import VerifyOtp from "@/pages/VerifyOtp";
import ForgotPassword from "@/pages/ForgotPassword";

import Dashboard from "@/pages/Dashboard";
import Projects from "@/pages/Projects";
import ProjectDetail from "@/pages/ProjectDetail";
import AnalyzeWizard from "@/pages/AnalyzeWizard";
import AnalysisReport from "@/pages/AnalysisReport";
import RfiKanban from "@/pages/RfiKanban";
import Outputs from "@/pages/Outputs";
import RiskDashboard from "@/pages/RiskDashboard";

import { SettingsProfile, SettingsSecurity } from "@/pages/Settings";

import { AdminUsers } from "@/pages/admin/AdminUsers";
import {
    AdminPermissionsList,
    AdminPermissionEditor,
} from "@/pages/admin/AdminPermissions";
import { AdminAuditLog } from "@/pages/admin/AdminAuditLog";
import { AdminAnalytics } from "@/pages/admin/AdminAnalytics";

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
            </BrowserRouter>
        </AuthProvider>
    );
}