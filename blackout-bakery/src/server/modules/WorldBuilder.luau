--!strict
-- WorldBuilder (server) -----------------------------------------------------
-- Builds the whole playable kitchen from code at boot: floor, a SpawnLocation,
-- ingredient bins (each with a ProximityPrompt + optional label), an oven, a
-- serve counter, a trash can, a mixing bowl (Taste prompt), and a lobby ready
-- pad. Returns every prompt so the stations can bind their handlers.
--
-- This means you can hit Play with an empty baseplate and the game exists --
-- no manual map building. Replace with your own art whenever you like; the
-- stations only care about the prompt references, not how they look.
-------------------------------------------------------------------------------

local Workspace = game:GetService("Workspace")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Config = require(ReplicatedStorage.BlackoutBakery.Config)

local WorldBuilder = {}

local INGREDIENT_COLORS: { [string]: Color3 } = {
	Flour = Color3.fromRGB(240, 236, 220),
	Sugar = Color3.fromRGB(250, 250, 255),
	Butter = Color3.fromRGB(245, 220, 120),
	Eggs = Color3.fromRGB(235, 205, 150),
	ChocolateChips = Color3.fromRGB(90, 55, 35),
	Milk = Color3.fromRGB(230, 240, 250),
	Salt = Color3.fromRGB(210, 210, 210),
	Vanilla = Color3.fromRGB(225, 200, 165),
}

local function makePart(name: string, size: Vector3, position: Vector3, color: Color3): Part
	local p = Instance.new("Part")
	p.Name = name
	p.Anchored = true
	p.Size = size
	p.Position = position
	p.Color = color
	p.TopSurface = Enum.SurfaceType.Smooth
	p.BottomSurface = Enum.SurfaceType.Smooth
	return p
end

-- `purpose` ("cook" / "taste" / "ready") lets the client enable/disable the
-- prompt locally for the right role. It's a UX hint only -- the server
-- re-checks the real role on every .Triggered, so it is never load-bearing.
local function makePrompt(parent: BasePart, actionText: string, objectText: string, purpose: string): ProximityPrompt
	local prompt = Instance.new("ProximityPrompt")
	prompt.ActionText = actionText
	prompt.ObjectText = objectText
	prompt.HoldDuration = 0
	prompt.MaxActivationDistance = 8
	prompt.RequiresLineOfSight = false
	prompt.Enabled = (purpose == "ready") -- gameplay prompts start disabled until roles are assigned
	prompt:SetAttribute("Purpose", purpose)
	prompt.Parent = parent
	return prompt
end

local function addBillboard(parent: BasePart, text: string)
	local gui = Instance.new("BillboardGui")
	gui.Size = UDim2.fromScale(4, 1)
	gui.StudsOffsetWorldSpace = Vector3.new(0, 2.4, 0)
	gui.AlwaysOnTop = true
	gui.Parent = parent

	local label = Instance.new("TextLabel")
	label.Size = UDim2.fromScale(1, 1)
	label.BackgroundTransparency = 1
	label.Text = text
	label.TextColor3 = Color3.fromRGB(255, 255, 255)
	label.TextStrokeTransparency = 0
	label.TextScaled = true
	label.Font = Enum.Font.GothamBold
	label.Parent = gui
end

export type Prompts = {
	ingredients: { [string]: ProximityPrompt },
	oven: ProximityPrompt,
	trash: ProximityPrompt,
	taste: ProximityPrompt,
	submit: ProximityPrompt,
	ready: ProximityPrompt,
}

function WorldBuilder.build(): Prompts
	local root = Workspace:FindFirstChild("Bakery")
	if root then
		root:Destroy()
	end
	root = Instance.new("Folder")
	root.Name = "Bakery"
	root.Parent = Workspace

	-- Floor.
	local floor = makePart("Floor", Vector3.new(80, 1, 60), Vector3.new(0, 0, 0), Color3.fromRGB(60, 60, 70))
	floor.Parent = root

	-- Spawn.
	local spawn = Instance.new("SpawnLocation")
	spawn.Name = "KitchenSpawn"
	spawn.Anchored = true
	spawn.Size = Vector3.new(6, 1, 6)
	spawn.Position = Vector3.new(0, 1, 20)
	spawn.Color = Color3.fromRGB(80, 140, 90)
	spawn.Parent = root

	-- Ingredient bins in a row along the back wall.
	local ingredients: { [string]: ProximityPrompt } = {}
	local startX = -((#Config.Ingredients - 1) * 6) / 2
	for i, ingredient in Config.Ingredients do
		local x = startX + (i - 1) * 6
		local bin = makePart(
			ingredient .. "Bin",
			Vector3.new(4, 4, 4),
			Vector3.new(x, 3, -20),
			INGREDIENT_COLORS[ingredient] or Color3.fromRGB(150, 150, 150)
		)
		bin.Parent = root
		ingredients[ingredient] = makePrompt(bin, "Add", ingredient .. " Bin", "cook")
		if Config.LabelBins then
			addBillboard(bin, ingredient)
		end
	end

	-- Mixing bowl (Taste target) center of the room.
	local bowl = makePart("MixingBowl", Vector3.new(5, 3, 5), Vector3.new(-8, 2.5, 0), Color3.fromRGB(180, 180, 190))
	bowl.Parent = root
	local taste = makePrompt(bowl, "Taste", "Mixing Bowl", "taste")
	addBillboard(bowl, "Mixing Bowl")

	-- Oven.
	local oven = makePart("Oven", Vector3.new(6, 6, 5), Vector3.new(8, 4, 0), Color3.fromRGB(45, 45, 50))
	oven.Parent = root
	local ovenPrompt = makePrompt(oven, "Bake", "Oven", "cook")
	addBillboard(oven, "Oven")

	-- Serve counter.
	local counter = makePart("ServeCounter", Vector3.new(8, 3, 4), Vector3.new(0, 2.5, 12), Color3.fromRGB(120, 90, 60))
	counter.Parent = root
	local submit = makePrompt(counter, "Serve", "Serve Counter", "cook")
	addBillboard(counter, "Serve Here")

	-- Trash.
	local trash = makePart("Trash", Vector3.new(3, 4, 3), Vector3.new(-16, 3, 8), Color3.fromRGB(70, 80, 70))
	trash.Parent = root
	local trashPrompt = makePrompt(trash, "Trash Bowl", "Trash", "cook")
	addBillboard(trash, "Trash")

	-- Lobby ready pad, off to the side near spawn.
	local pad = makePart("ReadyPad", Vector3.new(6, 1, 6), Vector3.new(16, 1, 20), Color3.fromRGB(90, 120, 160))
	pad.Parent = root
	local ready = makePrompt(pad, "Ready Up", "Ready Pad", "ready")
	addBillboard(pad, "Ready Up")

	return {
		ingredients = ingredients,
		oven = ovenPrompt,
		trash = trashPrompt,
		taste = taste,
		submit = submit,
		ready = ready,
	}
end

return WorldBuilder
