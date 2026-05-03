# frozen_string_literal: true

class GuestPolicy < ApplicationPolicy
  def index?
    front_desk_or_above?
  end

  def show?
    front_desk_or_above?
  end

  def create?
    front_desk_or_above?
  end

  def update?
    front_desk_or_above?
  end

  def destroy?
    manager_or_above?
  end

  class Scope < ApplicationPolicy::Scope
    def resolve
      if front_desk_or_above?
        scope.all
      else
        scope.none
      end
    end
  end
end