class ChangeConfirmationNumberUniqueness < ActiveRecord::Migration[8.0]
  def change
    # Remove global uniqueness constraint on confirmation_number
    remove_index :reservations, :confirmation_number

    # Add uniqueness constraint scoped to account
    # Confirmation numbers should be unique within an account, not globally
    add_index :reservations, [:account_id, :confirmation_number], unique: true
  end
end
