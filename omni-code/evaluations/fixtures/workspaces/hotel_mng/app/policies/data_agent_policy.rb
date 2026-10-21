class DataAgentPolicy < ApplicationPolicy
  # Data agent is accessed as a symbol, not a record
  # So we need to handle it specially
  
  def list_tables?
    # All authenticated users can list tables they have access to
    # The service layer will filter based on role
    any_staff?
  end
  
  def describe_table?
    # All authenticated users can describe tables they have access to
    # The service layer will validate table access
    any_staff?
  end
  
  def preview_query?
    # Only managers and above can execute queries
    # This provides read-only analytics access
    manager_or_above?
  end
  
  class Scope < Scope
    def resolve
      # Not applicable for data agent - it doesn't have records
      scope.none
    end
  end
end