--!strict
-- Net (server) --------------------------------------------------------------
-- Creates the RemoteEvents once at boot under
-- ReplicatedStorage/BlackoutBakery/Remotes and hands them back by role.
-- Clients only ever WaitForChild these; they cannot create or spoof them.
-------------------------------------------------------------------------------

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Config = require(ReplicatedStorage.BlackoutBakery.Config)

local Net = {}

local remotes: { [string]: RemoteEvent } = {}

function Net.init()
	local root = ReplicatedStorage:WaitForChild("BlackoutBakery")
	local folder = root:FindFirstChild("Remotes")
	if not folder then
		folder = Instance.new("Folder")
		folder.Name = "Remotes"
		folder.Parent = root
	end

	for _, eventName in Config.Remotes do
		local ev = folder:FindFirstChild(eventName)
		if not ev then
			ev = Instance.new("RemoteEvent")
			ev.Name = eventName
			ev.Parent = folder
		end
		remotes[eventName] = ev :: RemoteEvent
	end
end

function Net.get(name: string): RemoteEvent
	local ev = remotes[name]
	assert(ev, "Net.get: unknown remote '" .. tostring(name) .. "' (did you call Net.init?)")
	return ev
end

return Net
