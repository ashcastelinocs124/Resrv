import { useEffect } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { NavBar } from "./components/NavBar";
import { MaintenanceBanner } from "./components/MaintenanceBanner";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { runTour } from "./onboarding/runTour";
import { Dashboard } from "./pages/Dashboard";
import { Analytics } from "./pages/Analytics";
import { Login } from "./pages/Login";
import { AdminMachines } from "./pages/admin/Machines";
import { AdminStaff } from "./pages/admin/Staff";
import { AdminSettings } from "./pages/admin/Settings";
import { AdminColleges } from "./pages/admin/Colleges";
import { AdminFeedback } from "./pages/admin/Feedback";

function OnboardingGate() {
  const { username, role, onboardedAt, loading, markOnboardedLocal } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (loading || !username || onboardedAt) return;
    let cancelled = false;
    (async () => {
      try {
        await runTour(navigate, role === "admin");
        if (!cancelled) await markOnboardedLocal();
      } catch {
        /* swallow tour errors — don't block the app */
      }
    })();
    return () => {
      cancelled = true;
    };
    // markOnboardedLocal is stable enough; intentionally omitted from deps to
    // avoid re-running the tour when the function reference changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, username, onboardedAt, role]);

  return null;
}

function RequireStaff({ children }: { children: React.ReactElement }) {
  const { username, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return (
      <div className="py-12 text-center text-sm text-gray-500">Loading…</div>
    );
  }
  if (!username) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return children;
}

function RequireAdmin({ children }: { children: React.ReactElement }) {
  const { role, loading } = useAuth();
  if (loading) {
    return (
      <div className="py-12 text-center text-sm text-gray-500">Loading…</div>
    );
  }
  if (role !== "admin") {
    return <Navigate to="/admin/machines" replace />;
  }
  return children;
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <div className="min-h-screen bg-gray-100">
          <MaintenanceBanner />
          <NavBar />
          <OnboardingGate />
          <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/login" element={<Login />} />
              <Route
                path="/analytics"
                element={
                  <RequireStaff>
                    <Analytics />
                  </RequireStaff>
                }
              />
              <Route
                path="/admin"
                element={<Navigate to="/admin/machines" replace />}
              />
              <Route
                path="/admin/machines"
                element={
                  <RequireStaff>
                    <AdminMachines />
                  </RequireStaff>
                }
              />
              <Route
                path="/admin/staff"
                element={
                  <RequireStaff>
                    <RequireAdmin>
                      <AdminStaff />
                    </RequireAdmin>
                  </RequireStaff>
                }
              />
              <Route
                path="/admin/colleges"
                element={
                  <RequireStaff>
                    <RequireAdmin>
                      <AdminColleges />
                    </RequireAdmin>
                  </RequireStaff>
                }
              />
              <Route
                path="/admin/feedback"
                element={
                  <RequireStaff>
                    <AdminFeedback />
                  </RequireStaff>
                }
              />
              <Route
                path="/admin/settings"
                element={
                  <RequireStaff>
                    <RequireAdmin>
                      <AdminSettings />
                    </RequireAdmin>
                  </RequireStaff>
                }
              />
            </Routes>
          </main>
          <footer className="py-6 text-center text-sm text-gray-400">
            Built by <span className="font-bold">Agentic AI @ UIUC</span>
          </footer>
        </div>
      </AuthProvider>
    </BrowserRouter>
  );
}
