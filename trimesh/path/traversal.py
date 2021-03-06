import numpy as np
import networkx as nx

from collections import deque

from ..grouping import unique_ordered
from ..util import unitize
from ..constants import tol_path as tol
from .util import is_ccw


def vertex_graph(entities):
    """
    Given a set of entity objects generate a networkx.Graph
    that represents their vertex nodes.

    Parameters
    --------------
    entities : list
       Objects with 'closed' and 'nodes' attributes

    Returns
    -------------
    graph : networkx.Graph
        Graph where node indexes represent vertices
    closed : (n,) int
        Indexes of entities which are 'closed'
    """
    graph = nx.Graph()
    closed = []
    for index, entity in enumerate(entities):
        if entity.closed:
            closed.append(index)
        else:
            graph.add_edges_from(entity.nodes,
                                 entity_index=index)
    return graph, np.array(closed)


def vertex_to_entity_path(vertex_path,
                          graph,
                          entities,
                          vertices=None):
    """
    Convert a path of vertex indices to a path of entity indices.

    Parameters
    ----------
    vertex_path : (n,) int
        Ordered list of vertex indicies representing a path
    graph : nx.Graph
        Vertex connectivity
    entities : (m,) list
        Entity objects
    vertices :  (p, dimension) float
        Vertex points in space

    Returns
    ----------
    entity_path : (q,) int
        Entity indices which make up vertex_path
    """
    def edge_direction(a, b):
        """
        Given two edges, figure out if the first needs to be
         reversed to keep the progression forward.

         [1,0] [1,2] -1  1
         [1,0] [2,1] -1 -1
         [0,1] [1,2]  1  1
         [0,1] [2,1]  1 -1

        Parameters
        ------------
        a : (2,) int
        b : (2,) int

        Returns
        ------------
        a_direction : int
        b_direction : int
        """
        if a[0] == b[0]:
            return -1, 1
        elif a[0] == b[1]:
            return -1, -1
        elif a[1] == b[0]:
            return 1, 1
        elif a[1] == b[1]:
            return 1, -1
        else:
            raise ValueError('edges aren\'t connected!')

    if vertices is None:
        ccw_direction = 1
    else:
        ccw_check = is_ccw(vertices[np.append(vertex_path,
                                              vertex_path[0])])
        ccw_direction = (ccw_check * 2) - 1

    # populate the list of entities
    vertex_path = np.asanyarray(vertex_path)
    entity_path = deque()
    for i in np.arange(len(vertex_path) + 1):
        vertex_path_pos = np.mod(np.arange(2) + i, len(vertex_path))
        vertex_index = vertex_path[vertex_path_pos]
        entity_index = graph.get_edge_data(*vertex_index)['entity_index']
        entity_path.append(entity_index)
    # remove duplicate entities
    entity_path = unique_ordered(entity_path)[::ccw_direction]

    # traverse the entity path and reverse entities in place to align
    # with this path ordering
    round_trip = np.append(entity_path, entity_path[0])
    round_trip = zip(round_trip[:-1], round_trip[1:])
    for a, b in round_trip:
        da, db = edge_direction(entities[a].end_points,
                                entities[b].end_points)
        entities[a].points = entities[a].points[::da]
        entities[b].points = entities[b].points[::db]
    entity_path = np.array(entity_path)

    return entity_path


def connected_open(graph):
    broken = set()
    for node, degree in graph.degree().items():
        if degree == 2:
            continue
        if node in broken:
            continue
        [broken.add(i) for i in nx.node_connected_component(graph, node)]
    okay = set(graph.nodes()).difference(broken)
    return broken, okay


def closed_paths(entities, vertices):
    """
    Paths are lists of entity indices.
    We first generate vertex paths using graph cycle algorithms,
    and then convert them to entity paths.

    This will also change the ordering of entity.points in place
    so a path may be traversed without having to reverse the entity.

    Parameters
    -------------
    entities : (n,) entity objects
        Entity objects
    vertices : (m, dimension) float
        Vertex points in space

    Returns
    -------------
    entity_paths : sequence of (n,) int
        Ordered traversals of entities
    """
    # get a networkx graph of entities
    graph, closed = vertex_graph(entities)
    # add entities that are closed as single- entity paths
    entity_paths = deque(np.reshape(closed, (-1, 1)))
    # look for cycles in the graph, or closed loops
    vertex_paths = np.array(nx.cycles.cycle_basis(graph))

    # loop through every vertex cycle
    for vertex_path in vertex_paths:
        # a path has no length if it has fewer than 2 vertices
        if len(vertex_path) < 2:
            continue
        # convert vertex indicies to entity indices
        entity_paths.append(
            vertex_to_entity_path(vertex_path,
                                  graph,
                                  entities,
                                  vertices))
    entity_paths = np.array(entity_paths)
    return entity_paths


def discretize_path(entities, vertices, path, scale=1.0):
    """
    Turn a list of entity indices into a path of connected points.

    Parameters
    -----------
    entities : (j,) entity objects
       Objects like 'Line', 'Arc', etc.
    vertices: (n, dimension) float
        Vertex points in space.
    path : (m,) int
        Indexes of entities
    scale : float
        Overall scale of drawing used for
        numeric tolerances in certain cases

    Returns
    -----------
    discrete : (p, dimension) float
       Connected points in space that lie on the
       path and can be connected with line segments.
    """
    # make sure vertices are numpy array
    vertices = np.asanyarray(vertices)
    path_len = len(path)
    if path_len == 0:
        raise ValueError('Cannot discretize empty path!')
    if path_len == 1:
        # case where we only have one entity
        discrete = np.asanyarray(entities[path[0]].discrete(vertices,
                                                            scale=scale))
    else:
        # run through path appending each entity
        discrete = []
        for i, entity_id in enumerate(path):
            # the current (n, dimension) discrete curve of an entity
            current = entities[entity_id].discrete(vertices, scale=scale)
            # check if we are on the final entity
            if i >= (path_len - 1):
                # if we are on the last entity include the last point
                discrete.append(current)
            else:
                # slice off the last point so we don't get duplicate
                # points from the end of one entity and the start of another
                discrete.append(current[:-1])
        # stack all curves to one nice (n, dimension) curve
        discrete = np.vstack(discrete)
    # make sure 2D curves are are counterclockwise
    if vertices.shape[1] == 2 and not is_ccw(discrete):
        # reversing will make array non c- contiguous
        discrete = np.ascontiguousarray(discrete[::-1])

    return discrete


class PathSample:

    def __init__(self, points):
        # make sure input array is numpy
        self._points = np.array(points)
        # find the direction of each segment
        self._vectors = np.diff(self._points, axis=0)
        # find the length of each segment
        self._norms = np.linalg.norm(self._vectors, axis=1)
        # unit vectors for each segment
        nonzero = self._norms > tol.zero
        self._unit_vec = self._vectors.copy()
        self._unit_vec[nonzero] /= self._norms[nonzero].reshape((-1, 1))
        # total distance in the path
        self.length = self._norms.sum()
        # cumulative sum of section length
        # note that this is sorted
        self._cum_norm = np.cumsum(self._norms)

    def sample(self, distances):
        # return the indices in cum_norm that each sample would
        # need to be inserted at to maintain the sorted property
        positions = np.searchsorted(self._cum_norm, distances)
        positions = np.clip(positions, 0, len(self._unit_vec) - 1)
        offsets = np.append(0, self._cum_norm)[positions]
        # the distance past the reference vertex we need to travel
        projection = distances - offsets
        # find out which dirction we need to project
        direction = self._unit_vec[positions]
        # find out which vertex we're offset from
        origin = self._points[positions]
        # just the parametric equation for a line
        resampled = origin + (direction * projection.reshape((-1, 1)))

        return resampled

    def truncate(self, distance):
        """
        Return a truncated version of the path.
        Only one vertex (at the endpoint) will be added.
        """
        position = np.searchsorted(self._cum_norm, distance)
        offset = distance - self._cum_norm[position - 1]

        if offset < tol.merge:
            truncated = self._points[:position + 1]
        else:
            vector = unitize(np.diff(self._points[np.arange(2) + position],
                                     axis=0).reshape(-1))
            vector *= offset
            endpoint = self._points[position] + vector
            truncated = np.vstack((self._points[:position + 1],
                                   endpoint))

        assert (np.linalg.norm(np.diff(truncated, axis=0),
                               axis=1).sum() - distance) < tol.merge

        return truncated


def resample_path(points,
                  count=None,
                  step=None,
                  step_round=True):
    """
    Given a path along (n,d) points, resample them such that the
    distance traversed along the path is constant in between each
    of the resampled points. Note that this can produce clipping at
    corners, as the original vertices are NOT guaranteed to be in the
    new, resampled path.

    ONLY ONE of count or step can be specified
    Result can be uniformly distributed (np.linspace) by specifying count
    Result can have a specific distance (np.arange) by specifying step


    Parameters
    ----------
    points:   (n, d) float
        Points in space
    count : int,
        Number of points to sample evenly (aka np.linspace)
    step : float
        Distance each step should take along the path (aka np.arange)

    Returns
    ----------
    resampled : (j,d) float
        Points on the path
    """

    points = np.array(points, dtype=np.float)
    # generate samples along the perimeter from kwarg count or step
    if (count is not None) and (step is not None):
        raise ValueError('Only step OR count can be specified')
    if (count is None) and (step is None):
        raise ValueError('Either step or count must be specified')

    sampler = PathSample(points)
    if step is not None and step_round:
        if step >= sampler.length:
            return points[[0, -1]]

        count = int(np.ceil(sampler.length / step))

    if count is not None:
        samples = np.linspace(0, sampler.length, count)
    elif step is not None:
        samples = np.arange(0, sampler.length, step)

    resampled = sampler.sample(samples)

    check = np.linalg.norm(points[[0, -1]] - resampled[[0, -1]], axis=1)
    assert check[0] < tol.merge
    if count is not None:
        assert check[1] < tol.merge

    return resampled
