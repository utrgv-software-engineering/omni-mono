import { useEffect } from 'react'
import { Search, Filter, AlertCircle } from 'lucide-react'
import { useTraceStore } from '../store/traceStore'
import { useProjects } from '../hooks/useTraces'

export function TraceFilters() {
  const { 
    searchQuery, 
    setSearchQuery, 
    filters, 
    selectedProject, 
    setSelectedProject 
  } = useTraceStore()
  
  const { data: projectsData } = useProjects()
  
  // Set first project as default when projects are loaded
  useEffect(() => {
    if (projectsData?.projects?.length && !selectedProject) {
      setSelectedProject(projectsData.projects[0])
    }
  }, [projectsData, selectedProject, setSelectedProject])
  
  return (
    <div className="border-b border-border p-3">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Traces</h2>
        
        {/* Project Selector */}
        <select
          className="flex items-center gap-1 px-3 py-1 text-sm border border-input rounded-md bg-background hover:bg-muted/50 cursor-pointer"
          value={selectedProject || ''}
          onChange={(e) => setSelectedProject(e.target.value || null)}
        >
          <option value="">Select Project</option>
          {projectsData?.projects?.map((project: string) => (
            <option key={project} value={project}>
              {project}
            </option>
          ))}
        </select>
      </div>
      
      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
        <input
          type="text"
          placeholder="Search traces..."
          className="w-full pl-9 pr-3 py-2 text-sm border border-input rounded-md bg-background"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
      </div>
      
      {/* Filters */}
      <div className="flex items-center gap-2 mt-2">
        <button className="flex items-center gap-1 px-2 py-1 text-xs border border-border rounded hover:bg-muted/50">
          <Filter className="w-3 h-3" />
          Filters
        </button>
        {filters.hasErrors && (
          <span className="flex items-center gap-1 px-2 py-1 text-xs bg-destructive/10 text-destructive rounded">
            <AlertCircle className="w-3 h-3" />
            Errors Only
          </span>
        )}
      </div>
    </div>
  )
}