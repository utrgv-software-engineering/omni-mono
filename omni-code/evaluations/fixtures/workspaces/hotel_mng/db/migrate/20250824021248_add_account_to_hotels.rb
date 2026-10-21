class AddAccountToHotels < ActiveRecord::Migration[8.0]
  def change
    add_reference :hotels, :account, null: false, foreign_key: true
  end
end
