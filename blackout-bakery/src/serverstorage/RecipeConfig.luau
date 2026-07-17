--!strict
-- RecipeConfig (ServerStorage) -----------------------------------------------
-- THE SECRET. Dish definitions: ingredients + quantities per recipe.
--
-- This ModuleScript lives in ServerStorage, so Roblox never sends it to any
-- client. A Cook opening the dev console finds NOTHING here -- there is no
-- recipe data on their machine at all. The Reader learns the recipe only
-- because the server fires them a formatted ticket over TicketEvent.
--
-- Same principle as keeping rarity rolls server-side: the authoritative data
-- never leaves the server.
-------------------------------------------------------------------------------

export type Recipe = {
	id: string,
	name: string,
	tier: number, -- 1 = easy .. 3 = hard; drives difficulty ramp
	ingredients: { [string]: number }, -- ingredient -> required quantity
}

local RecipeConfig = {}

-- Ordered so higher tiers appear later in a shift.
local RECIPES: { Recipe } = {
	-- Tier 1: 2-3 ingredients, forgiving.
	{
		id = "SUGAR_COOKIE",
		name = "Sugar Cookie",
		tier = 1,
		ingredients = { Flour = 2, Sugar = 1, Butter = 1 },
	},
	{
		id = "PANCAKE",
		name = "Buttermilk Pancake",
		tier = 1,
		ingredients = { Flour = 2, Milk = 1, Eggs = 1 },
	},
	{
		id = "CHOC_CHIP_COOKIE",
		name = "Chocolate Chip Cookie",
		tier = 1,
		ingredients = { Flour = 2, Sugar = 1, Butter = 1, ChocolateChips = 2 },
	},

	-- Tier 2: 4-5 ingredients, a pinch of salt starts to matter.
	{
		id = "VANILLA_CUPCAKE",
		name = "Vanilla Cupcake",
		tier = 2,
		ingredients = { Flour = 2, Sugar = 2, Butter = 1, Eggs = 1, Vanilla = 1 },
	},
	{
		id = "BROWNIE",
		name = "Fudge Brownie",
		tier = 2,
		ingredients = { Flour = 1, Sugar = 2, Butter = 2, Eggs = 1, ChocolateChips = 3 },
	},
	{
		id = "BREAD_LOAF",
		name = "Soft Bread Loaf",
		tier = 2,
		ingredients = { Flour = 3, Milk = 1, Butter = 1, Salt = 1 },
	},

	-- Tier 3: 5-6 ingredients, tight quantities.
	{
		id = "BIRTHDAY_CAKE",
		name = "Birthday Cake",
		tier = 3,
		ingredients = { Flour = 3, Sugar = 3, Butter = 2, Eggs = 2, Milk = 1, Vanilla = 1 },
	},
	{
		id = "RED_VELVET",
		name = "Red Velvet Cake",
		tier = 3,
		ingredients = { Flour = 3, Sugar = 2, Butter = 2, Eggs = 2, ChocolateChips = 1, Salt = 1 },
	},
}

local BY_ID: { [string]: Recipe } = {}
for _, r in RECIPES do
	BY_ID[r.id] = r
end

function RecipeConfig.getById(id: string): Recipe?
	return BY_ID[id]
end

-- Returns a fresh list of recipes at or below `maxTier`, so early orders stay
-- easy and the shift gets harder as it goes on.
function RecipeConfig.pool(maxTier: number): { Recipe }
	local out: { Recipe } = {}
	for _, r in RECIPES do
		if r.tier <= maxTier then
			table.insert(out, r)
		end
	end
	return out
end

-- Human-readable ticket lines the Reader reads aloud, e.g. "2x Flour".
-- Sorted by ingredient name so the Reader always sees a stable list.
function RecipeConfig.toTicketLines(recipe: Recipe): { string }
	local keys: { string } = {}
	for ingredient in recipe.ingredients do
		table.insert(keys, ingredient)
	end
	table.sort(keys)

	local lines: { string } = {}
	for _, ingredient in keys do
		table.insert(lines, string.format("%dx %s", recipe.ingredients[ingredient], ingredient))
	end
	return lines
end

return RecipeConfig
