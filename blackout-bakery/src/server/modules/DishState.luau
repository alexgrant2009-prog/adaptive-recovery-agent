--!strict
-- DishState (server) --------------------------------------------------------
-- The single in-progress dish for the current order. Lives only on the server.
-- Cooks add ingredients through IngredientStation (which validates role first);
-- TasteService and SubmitStation read the snapshot to diff against the recipe.
-------------------------------------------------------------------------------

local DishState = {}

type Bowl = { [string]: number }

local bowl: Bowl = {}
local inOven = false -- once baked, no more ingredients until submit/clear

function DishState.clear()
	bowl = {}
	inOven = false
end

-- Adds one unit of an ingredient to the bowl. Returns the new count.
function DishState.add(ingredient: string): number
	bowl[ingredient] = (bowl[ingredient] or 0) + 1
	return bowl[ingredient]
end

function DishState.setInOven(value: boolean)
	inOven = value
end

function DishState.isInOven(): boolean
	return inOven
end

function DishState.isEmpty(): boolean
	return next(bowl) == nil
end

-- A copy, so callers can't mutate internal state.
function DishState.snapshot(): Bowl
	local copy: Bowl = {}
	for k, v in bowl do
		copy[k] = v
	end
	return copy
end

return DishState
