# frozen_string_literal: true

class RoomPolicy < ApplicationPolicy
  def index?
    any_staff?
  end

  def show?
    any_staff?
  end

  def create?
    manager_or_above?
  end

  def update?
    # Housekeeping can only update status fields
    if housekeeping? && !manager_or_above?
      # Check if only status fields are being updated
      return true # Will need to check permitted params in controller
    end
    manager_or_above? || housekeeping?
  end

  def destroy?
    manager_or_above?
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