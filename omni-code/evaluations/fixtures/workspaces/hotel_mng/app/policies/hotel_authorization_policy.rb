# frozen_string_literal: true

class HotelAuthorizationPolicy < ApplicationPolicy
  def index?
    any_staff?
  end

  def show?
    any_staff?
  end

  def new?
    owner_or_admin?
  end

  def create?
    owner_or_admin?
  end

  def update?
    manager_or_above?
  end

  def destroy?
    owner_or_admin?
  end

  class Scope < ApplicationPolicy::Scope
    def resolve
      if any_staff?
        scope.all # Will be scoped by tenant automatically
      else
        scope.none
      end
    end
  end
end