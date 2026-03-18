local srid = 4326

local buildings = osm2pgsql.define_table({
    name = 'osm_buildings',
    schema = 'geo',
    ids = { type = 'any', id_column = 'id' },
    columns = {
        { column = 'geom', type = 'geometry', projection = srid, not_null = true },
        { column = 'name', type = 'text' },
        { column = 'building_type', type = 'text' },
        { column = 'height', type = 'real' },
        { column = 'levels', type = 'int' },
        { column = 'tags', type = 'jsonb' },
    },
})

local roads = osm2pgsql.define_table({
    name = 'osm_roads',
    schema = 'geo',
    ids = { type = 'any', id_column = 'id' },
    columns = {
        { column = 'geom', type = 'linestring', projection = srid, not_null = true },
        { column = 'name', type = 'text' },
        { column = 'highway', type = 'text', not_null = true },
        { column = 'surface', type = 'text' },
        { column = 'oneway', type = 'bool' },
        { column = 'lanes', type = 'int' },
        { column = 'maxspeed', type = 'int' },
        { column = 'tags', type = 'jsonb' },
    },
})

local waterways = osm2pgsql.define_table({
    name = 'osm_waterways',
    schema = 'geo',
    ids = { type = 'any', id_column = 'id' },
    columns = {
        { column = 'geom', type = 'geometry', projection = srid, not_null = true },
        { column = 'name', type = 'text' },
        { column = 'waterway', type = 'text', not_null = true },
        { column = 'tags', type = 'jsonb' },
    },
})

local landuse = osm2pgsql.define_table({
    name = 'osm_landuse',
    schema = 'geo',
    ids = { type = 'any', id_column = 'id' },
    columns = {
        { column = 'geom', type = 'geometry', projection = srid, not_null = true },
        { column = 'name', type = 'text' },
        { column = 'landuse', type = 'text' },
        { column = 'leisure', type = 'text' },
        { column = 'natural', type = 'text' },
        { column = 'tags', type = 'jsonb' },
    },
})

local pois = osm2pgsql.define_table({
    name = 'osm_pois',
    schema = 'geo',
    ids = { type = 'any', id_column = 'id' },
    columns = {
        { column = 'geom', type = 'point', projection = srid, not_null = true },
        { column = 'name', type = 'text' },
        { column = 'amenity', type = 'text' },
        { column = 'shop', type = 'text' },
        { column = 'tourism', type = 'text' },
        { column = 'tags', type = 'jsonb' },
    },
})

local highway_types = {
    motorway = true, trunk = true, primary = true, secondary = true,
    tertiary = true, residential = true, service = true, unclassified = true,
    motorway_link = true, trunk_link = true, primary_link = true,
    secondary_link = true, tertiary_link = true, living_street = true,
    pedestrian = true, track = true, footway = true, cycleway = true, path = true,
}

local function parse_height(tags)
    local h = tags.height or tags['building:height']
    if h then
        local val = tonumber(h:match('^([%d%.]+)'))
        if val then return val end
    end
    local levels = tonumber(tags['building:levels'])
    if levels then return levels * 3.5 end
    return nil
end

local function clean_tags(tags, ...)
    local result = {}
    local skip = {}
    for _, k in ipairs({...}) do skip[k] = true end
    for k, v in pairs(tags) do
        if not skip[k] and not k:match('^name') and k ~= 'source' then
            result[k] = v
        end
    end
    return result
end

function osm2pgsql.process_node(object)
    local tags = object.tags

    if tags.amenity or tags.shop or tags.tourism then
        pois:insert({
            geom = object:as_point(),
            name = tags.name,
            amenity = tags.amenity,
            shop = tags.shop,
            tourism = tags.tourism,
            tags = clean_tags(tags, 'name', 'amenity', 'shop', 'tourism'),
        })
    end
end

function osm2pgsql.process_way(object)
    local tags = object.tags

    if tags.building then
        buildings:insert({
            geom = object:as_polygon(),
            name = tags.name,
            building_type = tags.building,
            height = parse_height(tags),
            levels = tonumber(tags['building:levels']),
            tags = clean_tags(tags, 'name', 'building', 'height', 'building:height', 'building:levels'),
        })
    end

    if tags.highway and highway_types[tags.highway] then
        roads:insert({
            geom = object:as_linestring(),
            name = tags.name,
            highway = tags.highway,
            surface = tags.surface,
            oneway = tags.oneway == 'yes',
            lanes = tonumber(tags.lanes),
            maxspeed = tonumber(tags.maxspeed and tags.maxspeed:match('^(%d+)')),
            tags = clean_tags(tags, 'name', 'highway', 'surface', 'oneway', 'lanes', 'maxspeed'),
        })
    end

    if tags.waterway then
        local geom = object:as_linestring()
        waterways:insert({
            geom = geom,
            name = tags.name,
            waterway = tags.waterway,
            tags = clean_tags(tags, 'name', 'waterway'),
        })
    end

    if tags.landuse or tags.leisure or tags['natural'] then
        if object.is_closed then
            landuse:insert({
                geom = object:as_polygon(),
                name = tags.name,
                landuse = tags.landuse,
                leisure = tags.leisure,
                ['natural'] = tags['natural'],
                tags = clean_tags(tags, 'name', 'landuse', 'leisure', 'natural'),
            })
        end
    end
end

function osm2pgsql.process_relation(object)
    local tags = object.tags

    if tags.building then
        buildings:insert({
            geom = object:as_multipolygon(),
            name = tags.name,
            building_type = tags.building,
            height = parse_height(tags),
            levels = tonumber(tags['building:levels']),
            tags = clean_tags(tags, 'name', 'building', 'height', 'building:height', 'building:levels'),
        })
    end

    if tags.waterway and tags.type == 'multipolygon' then
        waterways:insert({
            geom = object:as_multipolygon(),
            name = tags.name,
            waterway = tags.waterway,
            tags = clean_tags(tags, 'name', 'waterway'),
        })
    end

    if (tags.landuse or tags.leisure or tags['natural']) and tags.type == 'multipolygon' then
        landuse:insert({
            geom = object:as_multipolygon(),
            name = tags.name,
            landuse = tags.landuse,
            leisure = tags.leisure,
            ['natural'] = tags['natural'],
            tags = clean_tags(tags, 'name', 'landuse', 'leisure', 'natural'),
        })
    end
end
