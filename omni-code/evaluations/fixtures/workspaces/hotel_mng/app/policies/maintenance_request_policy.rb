# frozen_string_literal: true

class MaintenanceRequestPolicy < ApplicationPolicy
  def index?
    any_staff?
  end

  def show?
    any_staff?
  end

  def create?
    # Anyone on staff can report maintenance issues
    any_staff?
  end

  def update?
    front_desk_or_above?
  end

  def destroy?
    manager_or_above?
  end

  # Custom actions
  def assign?
    front_desk_or_above?
  end

  def complete?
    front_desk_or_above?
  end

  def cancel?
    front_desk_or_above?
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