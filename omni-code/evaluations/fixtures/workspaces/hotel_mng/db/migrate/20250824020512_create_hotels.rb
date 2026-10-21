class CreateHotels < ActiveRecord::Migration[8.0]
  def change
    create_table :hotels do |t|
      t.string :name
      t.string :subdomain

      t.timestamps
    end
    add_index :hotels, :subdomain, unique: true
  end
end
