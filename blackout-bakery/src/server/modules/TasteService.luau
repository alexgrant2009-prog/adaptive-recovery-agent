--!strict
-- TasteService (server) -----------------------------------------------------
-- Diffs the current DishState against the secret target recipe and sends a
-- FUZZY, flavour-text hint to the Taster only. Fuzzy (not an exact diff) so it
-- is funnier and harder to cheese -- the hint names a direction, never a number
-- (see spec "things to decide"). The hint is computed on the server and fired
-- with FireClient at the Taster, so no recipe data ever reaches other clients.
-------------------------------------------------------------------------------

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Config = require(ReplicatedStorage.BlackoutBakery.Config)
local Net = require(script.Parent.Net)
local RoleManager = require(script.Parent.RoleManager)
local DishState = require(script.Parent.DishState)

local TasteService = {}

-- Direction phrases per ingredient: [1] = too much, [-1] = too little.
local PHRASES: { [string]: { over: string, under: string } } = {
	Flour = { over = "it's dense -- too much flour", under = "the texture's too loose, needs more flour" },
	Sugar = { over = "whoa, way too sweet", under = "needs something sweeter" },
	Butter = { over = "greasy, it's too rich", under = "kind of thin, needs more richness" },
	Eggs = { over = "tastes too eggy", under = "it's not binding -- more egg" },
	ChocolateChips = { over = "chocolate overload", under = "could use more chocolate" },
	Milk = { over = "too runny", under = "a bit dry, needs moisture" },
	Salt = { over = "yikes, too salty", under = "pretty bland, needs a pinch of salt" },
	Vanilla = { over = "heavy on the vanilla", under = "needs a hint of vanilla" },
}

-- Returns a single fuzzy hint string for the biggest problem in the bowl.
-- `recipe.ingredients` is the secret target map.
function TasteService.computeHint(snapshot: { [string]: number }, targets: { [string]: number }): string
	-- 1) Something in the bowl that the recipe doesn't call for at all.
	for ingredient, amount in snapshot do
		if amount > 0 and (targets[ingredient] == nil or targets[ingredient] == 0) then
			return string.format("there's %s in here that doesn't belong", ingredient)
		end
	end

	-- 2) Find the ingredient with the largest gap from target.
	local worstIngredient: string? = nil
	local worstGap = 0
	local worstDir = 0
	for ingredient, target in targets do
		local have = snapshot[ingredient] or 0
		local gap = have - target
		if math.abs(gap) > math.abs(worstGap) then
			worstGap = gap
			worstIngredient = ingredient
			worstDir = if gap > 0 then 1 else -1
		end
	end

	if worstIngredient == nil or worstGap == 0 then
		return "tastes just right -- send it!"
	end

	local phrase = PHRASES[worstIngredient]
	if not phrase then
		-- Fallback if an ingredient has no flavour text defined.
		return if worstDir > 0
			then string.format("too much %s", worstIngredient)
			else string.format("needs more %s", worstIngredient)
	end

	return if worstDir > 0 then phrase.over else phrase.under
end

-- Called by the Taste ProximityPrompt handler. Validates the role server-side,
-- then fires the hint at every Taster (the Reader too, in 2-player games).
function TasteService.handleTaste(player: Player, targets: { [string]: number }?)
	if not RoleManager.isTaster(player) then
		return -- not authorised to taste; ignore silently
	end
	if not targets then
		return
	end
	if DishState.isEmpty() then
		Net.get(Config.Remotes.Taste):FireClient(player, "the bowl's empty -- nothing to taste yet")
		return
	end

	local hint = TasteService.computeHint(DishState.snapshot(), targets)
	for _, taster in RoleManager.getTasters() do
		Net.get(Config.Remotes.Taste):FireClient(taster, hint)
	end
end

return TasteService
