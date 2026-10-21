# frozen_string_literal: true

class CancellationPolicyPolicy < ApplicationPolicy
  def index?
    front_desk_or_above?
  end

  def show?
    front_desk_or_above?
  end

  def create?
    manager_or_above?
  end

  def update?
    manager_or_above?
  end

  def destroy?
    manager_or_above?
  end

  def make_default?
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