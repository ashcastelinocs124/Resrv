import { useQueues } from "./hooks/useQueues";
import { MachineColumn } from "./components/MachineColumn";
import { ConnectionStatus } from "./components/ConnectionStatus";

export default function App() {
  const { queues, error, loading, refresh } = useQueues(3000);

  return (
    <div className="min-h-screen bg-gray-100">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 shadow-sm">
        <div className="mx-auto max-w-7xl px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold text-gray-900 tracking-tight">
                Reserv
              </h1>
              <p className="text-sm text-gray-500">
                SCD Queue Management
              </p>
            </div>
            <div className="flex items-center gap-4">
              <ConnectionStatus />
              <button
                onClick={refresh}
                className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 transition-colors cursor-pointer"
              >
                Refresh
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Main */}
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        {loading && (
          <div className="flex justify-center py-20">
            <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-300 border-t-emerald-600" />
          </div>
        )}

        {error && !loading && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-center">
            <p className="font-medium text-red-800">
              Failed to load queues
            </p>
            <p className="mt-1 text-sm text-red-600">{error}</p>
            <button
              onClick={refresh}
              className="mt-3 rounded-md bg-red-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-red-700 cursor-pointer"
            >
              Retry
            </button>
          </div>
        )}

        {!loading && !error && (
          <div className="flex flex-wrap gap-4 justify-center lg:justify-start">
            {queues.map((q) => (
              <MachineColumn
                key={q.machine_id}
                queue={q}
                onRefresh={refresh}
              />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
