# frozen_string_literal: true

class HousekeepingTaskPolicy < ApplicationPolicy
  def index?
    any_staff?
  end

  def show?
    any_staff?
  end

  def create?
    front_desk_or_above?
  end

  def update?
    # Housekeeping staff can update status fields only
    # Front desk and above can do full updates
    if housekeeping?
      # Only allow status updates for housekeeping role
      record_changes = record.changes
      allowed_changes = ['status', 'completed_at', 'notes', 'updated_at']
      (record_changes.keys - allowed_changes).empty?
    else
      front_desk_or_above?
    end
  end

  def destroy?
    manager_or_above?
  end

  # Custom actions
  def mark_complete?
    housekeeping? || front_desk_or_above?
  end

  def assign?
    front_desk_or_above?
  end

  def start?
    housekeeping? || front_desk_or_above?
  end

  def complete?
    housekeeping? || front_desk_or_above?
  end

  class Scope < ApplicationPolicy::Scope
    def resolve
      if any_staff?
        scope.all
      else
        scope.none
      end
    end
  end
end