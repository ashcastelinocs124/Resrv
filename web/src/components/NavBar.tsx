import { Link, useLocation, useNavigate } from "react-router-dom";
import { ConnectionStatus } from "./ConnectionStatus";
import { useAuth } from "../auth/AuthContext";
import { runTour } from "../onboarding/runTour";

export function NavBar() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { username, role, logout } = useAuth();
  const isAdmin = role === "admin";

  const linkClass = (path: string) =>
    `px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
      pathname === path
        ? "bg-indigo-100 text-indigo-700"
        : "text-gray-600 hover:text-gray-900 hover:bg-gray-100"
    }`;

  const inAdmin = pathname.startsWith("/admin");

  return (
    <header className="bg-white border-b border-gray-200 shadow-sm">
      <div className="mx-auto max-w-7xl px-4 py-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-6">
            <div>
              <h1 className="text-2xl font-bold text-gray-900 tracking-tight">
                Reserv
              </h1>
              <p className="text-sm text-gray-500">SCD Queue Management</p>
            </div>
            <nav className="flex gap-1">
              <Link to="/" className={linkClass("/")}>
                Queues
              </Link>
              {username && (
                <Link to="/analytics" className={linkClass("/analytics")}>
                  Analytics
                </Link>
              )}
              {username && (
                <Link
                  to="/admin/machines"
                  className={
                    inAdmin
                      ? "px-3 py-1.5 rounded-lg text-sm font-medium bg-indigo-100 text-indigo-700"
                      : "px-3 py-1.5 rounded-lg text-sm font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-100"
                  }
                >
                  Admin
                </Link>
              )}
            </nav>
          </div>
          <div className="flex items-center gap-3">
            <ConnectionStatus />
            {username ? (
              <div className="flex items-center gap-2">
                <span className="text-sm text-gray-600">
                  {username}
                  <span className="ml-1 text-xs text-gray-400">({role})</span>
                </span>
                <button
                  onClick={() => runTour(navigate, isAdmin)}
                  className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
                  title="Replay the onboarding tour"
                >
                  Replay tour
                </button>
                <button
                  onClick={() => {
                    logout();
                    navigate("/");
                  }}
                  className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
                >
                  Logout
                </button>
              </div>
            ) : (
              <Link
                to="/login"
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
              >
                Staff Login
              </Link>
            )}
          </div>
        </div>
        {inAdmin && username && (
          <nav className="mt-3 flex gap-1 border-t border-gray-100 pt-3">
            <Link to="/admin/machines" className={linkClass("/admin/machines")}>
              Machines
            </Link>
            {isAdmin && (
              <Link to="/admin/staff" className={linkClass("/admin/staff")}>
                Staff
              </Link>
            )}
            {isAdmin && (
              <Link
                to="/admin/colleges"
                className={linkClass("/admin/colleges")}
              >
                Colleges
              </Link>
            )}
            <Link
              to="/admin/feedback"
              className={linkClass("/admin/feedback")}
            >
              Feedback
            </Link>
            {isAdmin && (
              <Link
                to="/admin/settings"
                className={linkClass("/admin/settings")}
              >
                Settings
              </Link>
            )}
          </nav>
        )}
      </div>
    </header>
  );
}
